"""Bounded appearance-space geometry refinement, never an optical inversion.

Prints proposed fits only. Focal/stop conditions remain separate, reference
plates remain private, and source positions/10-second frames are not fitted.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_reference_studies import build
from flare.engine import render_stack
from flare.grid import uv_to_grid
from flare.schema import validate_preset


def dataset(study,folder):
    ref=study['reference'];frames=[]
    for t in [6,8,12,14]:
        top,bottom=(247,403) if study['key']=='atlas_mercury' else (257,392)
        x0=215 if ref['side']=='left' else 536
        path=Path(folder)/(ref['pair']+f'-s{t:02}.png')
        rgb=np.asarray(Image.open(path).convert('RGB').crop((x0,top,x0+319,bottom))).astype(np.float32)/255
        white=rgb.min(-1).copy();white[:18,250:]=0
        yy,xx=np.unravel_index(white.argmax(),white.shape)
        if white.max()<.65 or xx<3 or xx>=316 or yy<3 or yy>=white.shape[0]-3:continue
        mask=np.zeros_like(white);mask[max(0,yy-4):yy+5,max(0,xx-4):xx+5]=1
        weights=mask*np.maximum(white-white.max()*.65,0);gy,gx=np.indices(white.shape)
        u=float(((gx+.5)*weights).sum()/max(weights.sum(),1e-8)/319)
        v=float(((gy+.5)*weights).sum()/max(weights.sum(),1e-8)/white.shape[0])
        x,y=uv_to_grid(u,v,white.shape[0],319)
        target=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
        # Softly emphasize visible boundaries over black background without
        # treating compressed texture, UI overlays or clipped peaks as truth.
        w=(.15+np.sqrt(target.mean(-1))*3)*(rgb.max(-1)<.92);w[:18,250:]=0
        frames.append((white.shape,dict(x=x,y=y,brightness=1),target,w))
    return frames


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--references',required=True)
    parser.add_argument('--keys',nargs='*');parser.add_argument('--rounds',type=int,default=2)
    args=parser.parse_args();torch.set_num_threads(1)
    result=dict(method='Bounded coordinate search in appearance space, four non-central source positions. Not calibrated lens geometry; not a quality score.',geometry={},colors={},loss={})
    result['stage']='coarse procedural, before refine_materials'
    for study in build(apply_materials=False):
        if args.keys and study['key'] not in args.keys:continue
        frames=dataset(study,args.references)
        if not frames:continue
        p=validate_preset(study['preset']);es=p['elements']
        def basis(e):
            single=dict(p,elements=[e]);parts=[]
            for shape,light,target,w in frames:
                im=render_stack(single,[light],shape[0]*3,957,'cuda',torch.float32)
                im=torch.nn.functional.interpolate(im.permute(2,0,1)[None],size=shape,mode='area')[0].permute(1,2,0).cpu().numpy()
                parts.append((im*np.sqrt(w)[...,None]).ravel())
            return np.concatenate(parts)
        target=np.concatenate([(t*np.sqrt(w)[...,None]).ravel() for _,_,t,w in frames])
        images=[basis(e) for e in es];total=sum(images);initial=float(np.mean((target-total)**2))
        # Fill and rim of one pupil must move together. Otherwise a pixel loss
        # can separate them into unrelated decorative rings.
        groups={}
        for j,e in enumerate(es):groups.setdefault(e['label'].removesuffix(' · rim'),[]).append(j)
        originals=copy.deepcopy(es)
        for round_index in range(args.rounds):
            step=1/(round_index+1)
            for label,indices in groups.items():
                e=es[indices[0]]
                dims=['scale']
                if not label.startswith('Source ·'):dims+=['offset','stretch_x','stretch_y']
                for dim in dims:
                    current=sum(images[j] for j in indices);residual=target-(total-current)
                    best=float(np.dot(residual-current,residual-current));chosen=None
                    for sign in [-1,1]:
                        candidates=[]
                        for j in indices:
                            c=copy.deepcopy(es[j]);original=originals[j]
                            if dim=='offset':c['offset']=float(np.clip(c['offset']+sign*.14*step,original['offset']-.35,original['offset']+.35))
                            elif dim.startswith('stretch'):
                                axis=dim.endswith('y');c['stretch'][axis]=float(np.clip(c['stretch'][axis]*(1+sign*.18*step),original['stretch'][axis]*.6,original['stretch'][axis]*1.6))
                            else:c['scale']=float(np.clip(c['scale']*(1+sign*.18*step),original['scale']*.6,original['scale']*1.6))
                            candidates.append(c)
                        trial_images=[basis(c) for c in candidates];trial=sum(trial_images)
                        # Analytic nonnegative scalar gain per grouped pupil.
                        gain=float(np.clip(np.dot(trial,residual)/max(np.dot(trial,trial),1e-12),.5,2))
                        error=residual-trial*gain
                        loss=float(np.dot(error,error))
                        if loss<best*.999:
                            best=loss;chosen=(candidates,trial_images,gain)
                    if chosen:
                        candidates,trial_images,gain=chosen;total-=current
                        for j,c,im in zip(indices,candidates,trial_images):
                            c['color']=[float(v*gain) for v in c['color']]
                            es[j]=c;images[j]=im*gain
                        total+=sum(images[j] for j in indices)
            print(study['key'],round_index+1,float(np.mean((target-total)**2))/max(initial,1e-15),file=sys.stderr,flush=True)
        for e in es:
            result['geometry'][e['id']]={k:e[k] for k in ('scale','offset','stretch')}
            result['colors'][e['id']]=e['color']
        result['loss'][study['key']]=dict(before=initial,after=float(np.mean((target-total)**2)),frames=len(frames))
    print(json.dumps(result,separators=(',',':')))


if __name__=='__main__':main()
