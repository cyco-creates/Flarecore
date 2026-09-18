# SPDX-License-Identifier: Apache-2.0
"""Opt-in real ComfyUI QA; local renders only, no paid inference.

Writes uniquely named QA input and temporary node outputs. The visibility
fixture is explicitly synthetic occlusion of an attributed photographic lamp.
"""
import argparse
import base64
import hashlib
import io
import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import torch
from PIL import Image, ImageOps, ImageDraw
from render_lens_candidate import fingerprint, save_image
from flare.lens_lab import validate_settings, render_lens
from flare.colorspace import srgb_to_linear
from flare.visibility import apply_source_visibility

BASE='http://127.0.0.1:8188'

def request(path,payload=None):
    for attempt in range(40):
        try:
            data=None if payload is None else json.dumps(payload).encode()
            req=urllib.request.Request(BASE+path,data=data,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=180) as r:return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code!=429:raise
            time.sleep(1)
    raise RuntimeError('Preview worker remained busy; no hidden retry render queued.')

def queue(prompt,out,tag,meta):
    state=request('/queue')
    if state['queue_running'] or state['queue_pending']:raise RuntimeError('Queue busy; not adding QA behind user work.')
    ident=request('/prompt',{'prompt':prompt})['prompt_id'];start=time.monotonic()
    print('Queued',tag,ident,flush=True)
    while time.monotonic()-start<1800:
        result=request('/history/'+ident).get(ident)
        if result:
            (out/(tag+'-history.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
            if result['status']['status_str']!='success':raise RuntimeError(result['status'])
            meta.setdefault('runs',[]).append(dict(tag=tag,prompt_id=ident,seconds=time.monotonic()-start,prompt=prompt))
            return result['outputs']
        time.sleep(1)
    raise RuntimeError('QA timeout; check history before resubmitting.')

def collect(outputs,node,out,prefix):
    arrays=[]
    for i,ref in enumerate(outputs[node]['images']):
        with urllib.request.urlopen(BASE+'/view?'+urllib.parse.urlencode(ref),timeout=30) as r:blob=r.read()
        path=out/f'{prefix}-{i:03d}.png';path.write_bytes(blob)
        arrays.append(np.asarray(Image.open(io.BytesIO(blob)).convert('RGB')))
    return arrays

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--assets',required=True)
    p.add_argument('--visibility-only',action='store_true')
    args=p.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True);assets=Path(args.assets)
    torch.set_num_threads(1)
    meta=dict(code=fingerprint(),generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              timing_note='Live server timings during concurrent offline QA, not standalone benchmarks.',
              display='ComfyUI PreviewImage: clipped sRGB, 8-bit. API uses nearest rounding; PreviewImage truncates.')
    def flush():(out/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    info=request('/object_info/FlareRender')['FlareRender'];defaults={}
    for name,spec in info['input']['required'].items():
        if name=='image':defaults[name]=['1',0]
        elif len(spec)>1 and 'default'in spec[1]:defaults[name]=spec[1]['default']
        elif isinstance(spec[0],list):defaults[name]=spec[0][0]
        else:raise ValueError('Unknown required input '+name)
    defaults.update(position_mode='manual',light_x=.7,light_y=.3,visibility_mode='off',
                    colorspace='srgb',clamp_output=True,scene_color=0.,blend_mode='add',intensity=1.,scale=1.)
    meta['catalog']=request('/flarecore/lens_lab');flush()
    for view in ([] if args.visibility_only else ['complete','ghosts']):
        for refine,quality in [(False,'draft'),(True,'standard')]:
            cfg=validate_settings(dict(quality=quality))
            if view=='ghosts':cfg.update(source_glow=0.,source_rays=0.)
            tag=f'parity-{view}-{quality}'
            preview=request('/flarecore/lens_lab/preview',dict(settings=cfg,u=.7,v=.3,refine=refine,view=view))
            blob=base64.b64decode(preview.pop('image').split(',',1)[1]);(out/(tag+'-preview.png')).write_bytes(blob)
            expected=np.asarray(Image.open(io.BytesIO(blob)).convert('RGB'))
            preset=dict(schema_version=1,global_ignored=None,elements=[],lens_lab=cfg)
            preset.pop('global_ignored')
            inputs=dict(defaults,preset_json=json.dumps(preset))
            prompt={'1':dict(class_type='EmptyImage',inputs=dict(width=512,height=288,batch_size=1,color=0)),
                    '2':dict(class_type='FlareRender',inputs=inputs),'3':dict(class_type='PreviewImage',inputs=dict(images=['2',1]))}
            rendered=collect(queue(prompt,out,tag,meta),'3',out,tag+'-final')[0]
            diff=np.abs(rendered.astype(int)-expected.astype(int))
            meta.setdefault('preview_parity',[]).append(dict(tag=tag,api=preview,max_display_difference=int(diff.max()),mean_display_difference=float(diff.mean())))
            flush();assert diff.max()<=1,(tag,int(diff.max()))
    # Controlled shutter: real lamp position is fixed; only the opaque strip
    # moves. This is not presented as a natural depth/segmentation dataset.
    photo=ImageOps.fit(Image.open(assets/'plate-lantern-original.jpg').convert('RGB'),(512,288),method=Image.Resampling.LANCZOS)
    original=np.asarray(photo);frames=[]
    x=(np.arange(512)+.5)/512
    for f in range(120):
        center=.10+.6*f/119
        coverage=np.clip((.065-np.abs(x-center))*512+.5,0,1)[None,:,None]
        array=np.round(original*(1-coverage)+np.array([5,6,8])*coverage).astype(np.uint8)
        frames.append(Image.fromarray(array))
    file=out/'flarecore-visibility-fixture.png'
    frames[0].save(file,save_all=True,append_images=frames[1:],duration=33,loop=0,compress_level=1)
    # Upload only this generated QA fixture, under its own unique input name.
    boundary='flarecore-local-qa';name='flarecore-visibility-'+hashlib.sha256(file.read_bytes()).hexdigest()[:10]+'.png'
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n').encode()+file.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(BASE+'/upload/image',data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req,timeout=30) as response:uploaded=json.load(response)
    image_name='/'.join(filter(None,[uploaded.get('subfolder'),uploaded['name']]))
    # The photographed lamp already has a bright core and halo. Add internal
    # reflections only, and cover the luminous body rather than one hot pixel.
    radius=.11
    cfg=validate_settings(dict(quality='draft',source_glow=0.,source_rays=0.))
    preset=dict(schema_version=1,elements=[],lens_lab=cfg);preset['global']=dict(intensity=.65,tint=[1.,.62,.28])
    inputs=dict(defaults,preset_json=json.dumps(preset),light_x=.344,light_y=.289,visibility_mode='image',occlusion_radius=radius,occlusion_smooth=.4)
    prompt={'1':dict(class_type='LoadImage',inputs=dict(image=image_name)),
            '2':dict(class_type='FlareRender',inputs=inputs),
            '3':dict(class_type='PreviewImage',inputs=dict(images=['2',0])),
            '4':dict(class_type='PreviewImage',inputs=dict(images=['2',1]))}
    meta['visibility']=dict(frames=120,source_radius=radius,source_finish='off: photograph already contains core and bloom',uploaded_input=image_name,fixture_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
        source='https://commons.wikimedia.org/wiki/File:Lantern_on_Charles_Bridge_at_Night.jpg',author='Jakub T. Jankiewicz',
        license='https://creativecommons.org/licenses/by-sa/4.0/',changes='Central crop and resize; artificial moving opaque shutter; additive Flarecore pass. Not a natural occlusion capture. No endorsement implied.')
    flush();outputs=queue(prompt,out,'visibility',meta)
    composites=collect(outputs,'3',out,'visibility-composite');passes=collect(outputs,'4',out,'visibility-pass')
    assert len(composites)==len(passes)==120,'Actual LoadImage batch did not preserve all 120 frames'
    tensor=srgb_to_linear(torch.from_numpy(np.stack([np.asarray(f) for f in frames])).float()/255)
    lights=[[dict(u=.344,v=.289)] for _ in frames]
    apply_source_visibility(lambda s,e:tensor[s:e],lights,16,radius,'image',.4)
    base=render_lens(cfg,[dict(u=.344,v=.289,brightness=.65,color=[1.,.62,.28])],288,512,'cuda').cpu().numpy()
    np.save(out/'visibility-clear-base-linear.npy',base)
    values=[f[0]['source_visibility'] for f in lights];meta['visibility']['values']=values
    meta['visibility']['actual_output_frames']=len(passes)
    for i in range(120):
        frames[i].save(out/f'visibility-plate-{i:03d}.png')
        save_image(out/f'visibility-predicted-pass-{i:03d}.png',base*values[i])
    sheet=Image.new('RGB',(1920,12*124),(15,17,22));draw=ImageDraw.Draw(sheet)
    for i,a in enumerate(composites):
        thumb=Image.fromarray(a).resize((192,108));xx=i%10*192;yy=i//10*124;sheet.paste(thumb,(xx,yy));draw.text((xx+3,yy+109),f'{i:03d} visibility {values[i]:.3f}',fill='white')
    sheet.save(out/'visibility-every-frame.jpg',quality=95)
    (out/'PHOTO_CREDITS.md').write_text(meta['visibility']['author']+' — '+meta['visibility']['source']+'\n\n'+meta['visibility']['license']+'\n\n'+meta['visibility']['changes'],encoding='utf-8')
    flush();print('Live preview parity and actual 120-frame visibility batch completed.',flush=True)

if __name__=='__main__':main()
