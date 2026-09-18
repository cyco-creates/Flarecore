"""Opt-in local smoke renders; no inference, paid services or workflow edits.

Writes generated ComfyUI temporary-image copies and a report to --output.
"""
import argparse
import json
import time
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from run_lens_lab_comfy_smoke import request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    queue=request('/queue')
    if queue['queue_running'] or queue['queue_pending']:
        raise SystemExit('ComfyUI is busy: nothing submitted.')
    info=request('/object_info/FlareRender')['FlareRender'];defaults={}
    for key,spec in info['input']['required'].items():
        if key=='image':defaults[key]=['1',0]
        elif len(spec)>1 and 'default' in spec[1]:defaults[key]=spec[1]['default']
        elif isinstance(spec[0],list):defaults[key]=spec[0][0]
        else:raise ValueError('No default for '+key)
    root=Path(__file__).resolve().parents[1];results=[]
    for name in ['study_kowa_cine_prominar','study_cooke_anamorphic_sf','study_zeiss_radiance']:
        inputs=dict(defaults,preset_json=(root/'presets'/(name+'.json')).read_text(encoding='utf-8'),
                    position_mode='manual',light_x=.3,light_y=.3,visibility_mode='off',
                    colorspace='srgb',clamp_output=True,scene_color=0.)
        prompt={'1':dict(class_type='EmptyImage',inputs=dict(width=768,height=432,batch_size=1,color=0)),
                '2':dict(class_type='FlareRender',inputs=inputs),
                '3':dict(class_type='PreviewImage',inputs=dict(images=['2',1]))}
        submitted=request('/prompt',dict(prompt=prompt));ident=submitted['prompt_id']
        print('Submitted',name,ident,flush=True)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            history=request('/history/'+ident).get(ident)
            if history:break
            time.sleep(.5)
        else:raise RuntimeError('Timed out; inspect history before retrying '+ident)
        if history['status']['status_str']!='success':raise RuntimeError(history['status'])
        ref=history['outputs']['3']['images'][0]
        url='http://127.0.0.1:8188/view?'+urllib.parse.urlencode(ref)
        with urllib.request.urlopen(url,timeout=20) as response:
            (out/(name+'.png')).write_bytes(response.read())
        results.append(dict(preset=name,prompt_id=ident,status='success',image=ref))
        print('PASS',name,flush=True)
    (out/'manifest.json').write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__':main()
