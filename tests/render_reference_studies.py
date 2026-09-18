"""Opt-in private reference comparisons; does not distribute reference plates.

Source centers are estimated from the displayed white peak, not the mouse.
The lens tests have unknown exposure/color processing: this is visual QA,
not photometric calibration or an automatic quality score.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from scripts.build_reference_studies import build
from flare.engine import render_stack
from flare.grid import uv_to_grid
from flare.schema import validate_preset
from render_lens_candidate import save_image
from conftest import load_package
resolve_textures=load_package().nodes.library.resolve_preset_textures


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--references',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--keys',nargs='*')
    p.add_argument('--times',nargs='+',type=int,default=[6,8,10,12,14])
    a=p.parse_args(); out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(1)
    manifest=[]
    for study in build():
        key=study['key']
        if a.keys and key not in a.keys:continue
        raw=json.loads((ROOT/'presets'/f'study_{key}.json').read_text(encoding='utf-8'))
        preset=validate_preset(raw)
        resolve_textures(preset,device='cuda',dtype=torch.float32)
        ref=study['reference']
        sheet=Image.new('RGB',(1280,len(a.times)*306),'#11141a');draw=ImageDraw.Draw(sheet)
        for row,t in enumerate(a.times):
            refpath=Path(a.references)/(ref['pair']+f'-s{t:02}.png')
            full=Image.open(refpath).convert('RGB')
            x0=215 if ref['side']=='left' else 536
            top,bottom=(247,403) if key=='atlas_mercury' else (257,392)
            crop=full.crop((x0,top,x0+319,bottom))
            pixels=np.asarray(crop).astype(np.float32)/255
            # Ignore the website Save overlay, never erase it in the source image.
            white=pixels.min(axis=2).copy();white[:18,250:]=0
            yy,xx=np.unravel_index(white.argmax(),white.shape)
            if white.max()<.65 or xx<3 or xx>=316 or yy<3 or yy>=white.shape[0]-3:
                draw.text((8,row*306+4),f'{raw["name"]} | {t}s | source not reliably localizable',fill='white')
                sheet.paste(crop.resize((640,round(640*white.shape[0]/319))),(0,row*306+25))
                draw.text((660,row*306+90),'Outside/clipped source: no invented comparison position.',fill='white')
                manifest.append(dict(key=key,time=t,skipped=True,reason='source absent, clipped or dim',reference=str(refpath)))
                continue
            near=np.zeros_like(white);near[max(yy-4,0):yy+5,max(xx-4,0):xx+5]=1
            weights=near*np.maximum(white-white.max()*.65,0)
            gy,gx=np.indices(white.shape);den=max(weights.sum(),1e-8)
            u=float(((gx+.5)*weights).sum()/den/319)
            v=float(((gy+.5)*weights).sum()/den/white.shape[0])
            h=540;w=round(h*319/white.shape[0])
            x,y=uv_to_grid(u,v,h,w)
            rgb=render_stack(preset,[dict(x=x,y=y,brightness=1)],h,w,'cuda',torch.float32).cpu().numpy()
            filename=f'{key}-s{t:02}.png'
            save_image(out/filename,rgb)
            display_h=round(640*white.shape[0]/319)
            draw.text((8,row*306+4),f'{raw["name"]} | {t}s | reference left / Flarecore right',fill='white')
            sheet.paste(crop.resize((640,display_h)),(0,row*306+25))
            sheet.paste(Image.open(out/filename).resize((640,display_h)),(640,row*306+25))
            manifest.append(dict(key=key,time=t,source_uv=[u,v],reference=str(refpath),
                crop=[x0,top,x0+319,bottom],preset_sha256=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()))
        sheet.save(out/(key+'-comparison.jpg'),quality=96)
        print(key,flush=True)
    code={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'flare/engine.py','flare/elements.py','flare/pupil.py','flare/motion.py','flare/schema.py',
        'scripts/build_reference_studies.py','docs/reference_study_fits.json')}
    (out/'manifest.json').write_text(json.dumps(dict(display='fixed linear clip then sRGB; unit white source; not exposure calibrated',code=code,items=manifest),indent=2),encoding='utf-8')


if __name__=='__main__':main()
