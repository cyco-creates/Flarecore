# SPDX-License-Identifier: Apache-2.0
"""Offline source-envelope/balance study, not shipping defaults or scored QA."""
import argparse
import sys
import math
import json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import torch
from render_lens_candidate import Evidence, save_image, stats
from flare.lens_lab import validate_settings
from flare.lens_finish import source_finish


def envelope(settings,u,v,height,width,device,dtype,variant=0):
    yy=(torch.arange(height,device=device,dtype=dtype)+.5)/height-v
    xx=((torch.arange(width,device=device,dtype=dtype)+.5)/width-u)*width/height
    y,x=torch.meshgrid(yy,xx,indexing='ij');r=(x*x+y*y+1e-12).sqrt()
    size=max(settings['source_size'],.65/height)
    core=2.2*torch.exp(-.5*(r/(size*.8)).square())
    inner=(1.3 if variant==0 else 1.1)/(1+(r/(size*(4.4 if variant==0 else 5))).square()).pow(1.8 if variant==0 else 2.)
    outer=.008*torch.exp(-r/(size*22))
    angle=torch.atan2(y,x)-math.radians(settings['rotation'])
    blades=settings['blades'];arms=blades if blades and blades%2==0 else max(6,blades*2)
    broad=(.5+.5*torch.cos(angle*arms+.16*torch.sin(angle*3))).pow(3)
    medium=(.5+.5*torch.cos(angle*(arms*2+2)+.65*torch.sin(angle*5))).pow(14)
    fine=(.5+.5*torch.cos(angle*(arms*4+2)+.7*torch.sin(angle*3)+.3*torch.sin(angle*7))).pow(32)
    modulation=.72+.17*torch.cos(angle*5+1.7)+.11*torch.sin(angle*9)
    reach=size*(18+5*torch.sin(angle*3+.7)+2*torch.cos(angle*7))
    angular=(.20*broad+.04*medium+.018*fine+.0287) if variant==2 else (.28*broad+.065*medium+.018*fine)
    rays=angular*modulation*torch.exp(-r/reach)/(1+(r/(size*4)).square())
    tint=torch.tensor([.48,.72,1.],device=device,dtype=dtype)
    neutral=settings['source_glow']*core
    wings=settings['source_glow']*(inner+outer)+settings['source_rays']*rays
    blend=(1-torch.exp(-r/(size*2)))[...,None]
    return neutral[...,None]+wings[...,None]*(1-blend+blend*tint)


def main():
    p=argparse.ArgumentParser();p.add_argument('--assets',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();torch.set_num_threads(1)
    e=Evidence(args.output,'cuda');e.meta['study']='Unshipped envelope function in tests/study_lens_envelope.py; no per-frame gain normalization.'
    e.meta['study_code']=__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest()
    for name,uv in [('black',(.7,.3)),('lantern',(.344,.289))]:
        h,w=1080,1920;cfg=validate_settings(dict(source_glow=0,source_rays=0))
        # Suppress shared interfaces selectively: keep every path active.
        balanced=validate_settings(dict(cfg,surfaces={
            '0':{'reflection':.55},'1':{'reflection':.35},'2':{'reflection':.55},
            '4':{'reflection':.18},'6':{'reflection':.24},'11':{'reflection':.18}}))
        ghost=e.render(name+'-balanced-trace',balanced,[dict(u=uv[0],v=uv[1])],h=h,w=w)
        if name=='black':
            original=np.load(Path(args.assets)/'v9-stills/offaxis.npy')
            plate=np.zeros_like(ghost);color=np.ones(3,dtype=np.float32)
        else:
            original=None;plate=np.load(Path(args.assets)/'v8-plates/lantern-plate.npy')
            color=np.array([1.,.62,.28],dtype=np.float32)
        scfg=validate_settings(dict(source_size=.008 if name=='black' else .011,source_rays=1 if name=='black' else .5))
        for style in ['v9','envelope-a','envelope-b']:
            finish=(source_finish(scfg,*uv,h,w,'cuda',torch.float32) if style=='v9'
                    else envelope(scfg,*uv,h,w,'cuda',torch.float32,variant=int(style.endswith('b')))).cpu().numpy()
            for level,gain in [('restrained',.65),('strong',2.6)]:
                # Overall strength changes BOTH ghosts and finish, unlike ghost EV.
                out=(ghost+finish)*color*gain
                key=f'{name}-{style}-{level}'
                np.save(e.folder/(key+'.npy'),out);save_image(e.folder/(key+'.png'),out)
                if name!='black':save_image(e.folder/(key+'-composite.png'),plate+out)
                e.meta.setdefault('composed_studies',[]).append(dict(name=key,source_settings=scfg,overall_gain=gain,
                    ghost_item=name+'-balanced-trace',source_implementation=style,**stats(out)))
            if original is not None:
                save_image(e.folder/('original-ghosts-'+style+'.png'),original+finish)
        e.flush()
    e.sheet([f'black-{s}-restrained' for s in ['v9','envelope-a','envelope-b']]+[
        f'lantern-{s}-strong-composite' for s in ['v9','envelope-a','envelope-b']],filename='study-contact.png',cols=3)

if __name__=='__main__':main()
