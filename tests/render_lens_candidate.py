# SPDX-License-Identifier: Apache-2.0
"""Opt-in, reproducible visual evidence generator; no external inference.

Artifacts contain raw linear float RGB, fixed sRGB display images, settings,
source values, timings and hashes of the renderer that actually produced them.
This is evidence generation, not a quality score or an automatic pass.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flare.lens_lab import render_lens, validate_settings, ghost_pairs
from flare.lens_finish import source_finish
from flare.colorspace import linear_to_srgb, srgb_to_linear


def fingerprint():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'flare/lens_lab.py', ROOT/'flare/lens_finish.py', ROOT/'flare/lens_raster.py', ROOT/'flare/engine.py',
                      ROOT/'flare/visibility.py', ROOT/'flare/colorspace.py',
                      ROOT/'nodes/render.py', ROOT/'nodes/lens_lab_api.py', ROOT/'web/flarecore_lens_lab.js',
                      ROOT/'web/flarecore_ui.js', ROOT/'web/flarecore_help.js']}


def stats(a):
    l = a @ np.array([.2126,.7152,.0722], dtype=np.float32)
    yy,xx=np.indices(l.shape)
    total=float(l.sum())
    return dict(energy=total,peak=float(l.max()),p99=float(np.quantile(l,.99)),
                centroid=[float((l*xx).sum()/max(total,1e-20)),float((l*yy).sum()/max(total,1e-20))])


def save_image(path, a):
    rgb=linear_to_srgb(torch.as_tensor(a).clamp(0,1)).mul(255).add(.5).to(torch.uint8).numpy()
    Image.fromarray(rgb).save(path)


class Evidence:
    def __init__(self, folder, device):
        self.folder=Path(folder); self.folder.mkdir(parents=True,exist_ok=True)
        self.device=device
        self.items=[]
        self.meta=dict(code=fingerprint(),display='linear RGB clamped 0..1 then standard sRGB; no per-image normalization',
                       device=device,device_name=torch.cuda.get_device_name() if device=='cuda' else 'CPU',
                       torch=torch.__version__,items=self.items)
    def render(self, name, config, lights=None, h=576, w=1024, grid=None, plate=None):
        config=validate_settings(config)
        lights=lights or [dict(u=.7,v=.3,brightness=1.,color=[1.,1.,1.])]
        start=time.perf_counter()
        a=render_lens(config,lights,h,w,self.device,grid_size=grid).cpu().numpy()
        elapsed=time.perf_counter()-start
        np.save(self.folder/(name+'.npy'), a)
        save_image(self.folder/(name+'.png'),a)
        item=dict(name=name,settings=config,lights=lights,resolution=[w,h],grid_override=grid,seconds=elapsed,**stats(a))
        self.items.append(item)
        if plate is not None:
            save_image(self.folder/(name+'-composite.png'),a+plate)
        self.flush()
        print(name,round(elapsed,3),'s',item['energy'],flush=True)
        return a
    def flush(self):
        (self.folder/'manifest.json').write_text(json.dumps(self.meta,indent=2),encoding='utf-8')
    def sheet(self,names,filename='contact.png',cols=3):
        sheet=Image.new('RGB',(cols*512,((len(names)+cols-1)//cols)*316),(16,18,23))
        draw=ImageDraw.Draw(sheet)
        for i,name in enumerate(names):
            im=Image.open(self.folder/(name+'.png')).convert('RGB'); im.thumbnail((512,288))
            x=(i%cols)*512; y=(i//cols)*316
            sheet.paste(im,(x,y)); draw.text((x+8,y+294),name,fill='white')
        sheet.save(self.folder/filename)


def mixed_settings(**kw):
    # Designed single-layer responses, not measured commercial coatings.
    coatings=[450,1050,650,500,950,650,1050,480,650,1050,500,850,450]
    settings = dict(exposure=6.,quality='fine',coating_nm=650.,
                    surfaces={str(i):dict(coating_nm=d) for i,d in enumerate(coatings) if i!=5})
    settings.update(kw)
    return settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('suite',choices=['looks','stills','paths','motion','beauty','convergence','micro'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--device',default='cuda')
    args=parser.parse_args()
    torch.set_num_threads(1)
    e=Evidence(args.output,args.device)
    if args.suite=='beauty':
        base=mixed_settings()
        e.render('source-developed',base)
        balanced=mixed_settings()
        for i in ['1','4']:
            balanced['surfaces'][i]['reflection']=.35
        e.render('balanced-ghosts',dict(balanced,source_glow=0,source_rays=0))
        e.render('balanced-complete',balanced)
        e.sheet([i['name'] for i in e.items])
    if args.suite=='looks':
        for name,cfg in [('cool',dict(coating_nm=650)),('neutral',dict(coating_strength=0)),
                         ('green',dict(coating_nm=1050)),('mixed',mixed_settings())]:
            c=dict(exposure=6,quality='fine'); c.update(cfg)
            e.render(name,c)
        e.sheet([i['name'] for i in e.items],cols=2)
    if args.suite=='stills':
        c=validate_settings(dict(source_glow=0,source_rays=0))
        for name,u,v in [('center',.5,.5),('offaxis',.7,.3),('corner',.95,.08),('offscreen',1.1,.3)]:
            e.render(name,c,[dict(u=u,v=v)],h=1080,w=1920)
        for name,settings in [('circle',dict(blades=0)),('f28',dict(f_stop=2.8)),('f8',dict(f_stop=8)),
                              ('f16',dict(f_stop=16)),('f22',dict(f_stop=22)),('rot37',dict(rotation=37))]:
            e.render(name,dict(c,**settings),h=1080,w=1920)
        for name,settings in [('bare-f28',dict(f_stop=2.8)),('bare-circle',dict(blades=0)),('bare-rot37',dict(rotation=37))]:
            e.render(name,dict(c,coating_strength=0,surfaces={},**settings),h=1080,w=1920)
        e.sheet([i['name'] for i in e.items])
    if args.suite=='paths':
        allpaths=[f'{a}:{b}' for a,b in ghost_pairs()]
        for path in allpaths:
            c=mixed_settings(source_glow=0,source_rays=0,disabled_pairs=[p for p in allpaths if p!=path])
            e.render('path-'+path.replace(':','-'),c, h=288,w=512,grid=96)
        e.sheet([i['name'] for i in sorted(e.items,key=lambda i:-i['energy'])],cols=4)
    if args.suite=='motion':
        for mode in ['horizontal','diagonal-stopped']:
            c=validate_settings({});c['quality']='standard'
            if mode=='diagonal-stopped':c.update(f_stop=16,exposure=9.)
            names=[]
            for f in range(120):
                t=f/119
                u=-.1+1.2*t;v=.3 if mode=='horizontal' else .1+.8*t
                name=f'{mode}-{f:03d}';names.append(name)
                e.render(name,c,[dict(u=u,v=v)],h=576,w=1024)
            e.sheet(names[::10],filename=mode+'-contact.png',cols=3)
        for f in range(16):
            e.render(f'static-{f:02d}',validate_settings({}),h=576,w=1024,grid=96)
        e.meta['fps']=30;e.flush()
    if args.suite=='convergence':
        paths=[f'{a}:{b}' for a,b in ghost_pairs()]
        for path in ['1:4','2:6','0:12','11:12','2:7']:
            c=validate_settings(dict(source_glow=0,source_rays=0,disabled_pairs=[p for p in paths if p!=path]))
            for grid in [48,96,192,384]:
                e.render(f'path-{path.replace(":","-")}-n{grid}',c,h=1080,w=1920,grid=grid)
            high=e.render(f'path-{path.replace(":","-")}-2x',c,h=2160,w=3840,grid=384)
            reduced=high.reshape(1080,2,1920,2,3).mean((1,3))
            name=f'path-{path.replace(":","-")}-2x-linear-reduced'
            np.save(e.folder/(name+'.npy'),reduced);save_image(e.folder/(name+'.png'),reduced)
        e.sheet([i['name'] for i in e.items])
    if args.suite=='micro':
        keep={(1,4),(2,6),(0,12),(11,12),(2,7)}
        c=validate_settings(dict(source_glow=0,source_rays=0,disabled_pairs=[f'{a}:{b}' for a,b in ghost_pairs() if (a,b) not in keep]))
        names=[]
        for f in range(48):
            name=f'micro-{f:03d}';names.append(name)
            e.render(name,c,[dict(u=.6988+.0024*f/47,v=.3)],h=1080,w=1920)
        e.sheet(names[::4]);e.meta['fps']=30;e.flush()


if __name__=='__main__':main()
