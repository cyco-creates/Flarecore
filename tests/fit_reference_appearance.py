"""Fit bounded per-element linear RGB weights from PRIVATE displayed tests.

No plates are copied into assets. Fits are display-appearance approximations,
not measured lens transmission. The stdout result is reviewed then applied as
a patch; this script never changes the library itself.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_reference_studies import build
from flare.schema import validate_preset
from flare.engine import render_stack
from flare.grid import uv_to_grid


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--references',required=True)
    parser.add_argument('--fit-source',action='store_true',help='Also fit source energy, preserving authored source hues and shapes.')
    args=parser.parse_args();torch.set_num_threads(1)
    result=dict(method='Hue-preserving scalar energy fit, bounded 0.15..4; source finish fixed. Saturated highlights excluded. Unknown exposure and grading; NOT calibrated optical data.',
                source='https://lenses.cineflares.com/',train_times=[6,8,12,14],holdout_time=10,colors={},reference_sha256={},excluded=[])
    result['fit_source']=args.fit_source
    if args.fit_source:
        result['method']='Hue-preserving scalar appearance fit including source energy, bounded 0.05..8. Source geometry authored. Display references are not photometric or optical calibration. Basis 3x area-downsampled; saturated pixels and website overlay excluded.'
    # Fit the coarse procedural stage. Final visual/material corrections are
    # intentionally subsequent and must be reviewed again after any new fit.
    result['stage']='coarse procedural, before refine_materials'
    for study in build(apply_fits=False,apply_materials=False):
        ref=study['reference'];p=validate_preset(study['preset']);count=len(p['elements'])
        gram=np.zeros((count,count));rhs=np.zeros(count)
        for t in result['train_times']:
            file=Path(args.references)/(ref['pair']+f'-s{t:02}.png')
            result['reference_sha256'][file.name]=hashlib.sha256(file.read_bytes()).hexdigest()
            x0=215 if ref['side']=='left' else 536
            top,bottom=(247,403) if study['key']=='atlas_mercury' else (257,392)
            rgb=np.asarray(Image.open(file).convert('RGB').crop((x0,top,x0+319,bottom))).astype(np.float32)/255
            white=rgb.min(-1).copy();white[:18,250:]=0
            yy,xx=np.unravel_index(white.argmax(),white.shape)
            if white.max()<.65 or xx<3 or xx>=316 or yy<3 or yy>=white.shape[0]-3:
                result['excluded'].append(dict(key=study['key'],time=t,reason='Source absent, clipped or too dim for reliable automatic localization'))
                continue
            mask=np.zeros_like(white);mask[max(0,yy-4):yy+5,max(0,xx-4):xx+5]=1
            weights=mask*np.maximum(white-white.max()*.65,0);gy,gx=np.indices(white.shape)
            u=float(((gx+.5)*weights).sum()/max(weights.sum(),1e-8)/319)
            v=float(((gy+.5)*weights).sum()/max(weights.sum(),1e-8)/white.shape[0])
            x,y=uv_to_grid(u,v,white.shape[0],319)
            target=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
            w=(.15+np.sqrt(target.mean(-1))*3)*(rgb.max(-1)<.92)
            w[:18,250:]=0
            basis=[]
            for e in p['elements']:
                single=copy.deepcopy(p);single['elements']=[copy.deepcopy(e)]
                high=render_stack(single,[dict(x=x,y=y,brightness=1)],white.shape[0]*3,957,'cuda',torch.float32)
                rendered=torch.nn.functional.interpolate(high.permute(2,0,1)[None],size=white.shape,mode='area')[0].permute(1,2,0).cpu().numpy()
                basis.append(rendered)
            for c in range(3):
                a=np.stack([b[...,c].ravel() for b in basis],1).astype(np.float64)
                aw=a*w.ravel()[:,None]
                gram+=a.T@aw;rhs+=aw.T@target[...,c].ravel()
        colors=np.array([e['color'] for e in p['elements']],dtype=np.float64)
        g=gram;b=rhs;prior=np.ones(count)
        reg=np.maximum(np.diag(g)*.03,1e-8);g+=np.diag(reg);b+=reg*prior
        q=prior.copy()
        fixed=[e['label'].startswith('Source ·') and not args.fit_source for e in p['elements']]
        floors=[1. if e['label'].endswith(' · rim') else (.7 if e['type']=='ring' else .15) for e in p['elements']]
        for iteration in range(240):
            for j in range(count):
                if not fixed[j]:
                    q[j]=np.clip(q[j]+(b[j]-g[j]@q)/max(g[j,j],1e-10),.05 if args.fit_source else floors[j],8 if args.fit_source else 4)
        colors*=q[:,None]
        for e,c in zip(p['elements'],colors):
            result['colors'][e['id']]=[round(float(v),6) for v in c]
        print(study['key'],file=sys.stderr,flush=True)
    print(json.dumps(result,separators=(',',':')))


if __name__=='__main__':main()
