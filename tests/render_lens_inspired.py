# SPDX-License-Identifier: Apache-2.0
"""Opt-in fixed-exposure visual QA for all 15 artistic lens interpretations."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from render_lens_candidate import ROOT, save_image, stats, fingerprint
from flare.schema import validate_preset
from flare.engine import render_stack
from flare.grid import uv_to_grid
from flare.colorspace import srgb_to_linear
import numpy as np
import torch
from PIL import Image,ImageOps,ImageDraw


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--assets',required=True)
    a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(1)
    catalog=json.loads((ROOT/'docs/lens_inspired_sources.json').read_text(encoding='utf-8'))
    photo_path=Path(a.assets)/'plate-lantern-original.jpg'
    photo=ImageOps.fit(Image.open(photo_path).convert('RGB'),(1920,1080),method=Image.Resampling.LANCZOS)
    plate=srgb_to_linear(torch.from_numpy(np.asarray(photo).copy()).float()/255).numpy()
    save_image(out/'lantern-plate.png',plate)
    meta=dict(code=fingerprint(),generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        display='Fixed linear addition; clip 0..1 only for sRGB PNG. No per-preset normalization.',
        items=[],plate_sha256=hashlib.sha256(photo_path.read_bytes()).hexdigest())
    for r in catalog['presets']:
        path=ROOT/r['file'];raw=json.loads(path.read_text(encoding='utf-8'));name=path.stem
        preset=validate_preset(raw)
        for label,u,v,strength in [('black',.73,.29,1.),('lantern',.344,.289,.5)]:
            # The photographed lamp already has a source; disable only the
            # clearly named source finish elements for this compositing test.
            if label=='lantern':
                preset=validate_preset(raw)
                for e in preset['elements']:
                    if e['id'] in ('source_core','source_inner_light','source_envelope','fine_source_rays'):e['enabled']=False
            x,y=uv_to_grid(u,v,1080,1920)
            light=dict(x=x,y=y,brightness=strength,color=[1.,.62,.28] if label=='lantern' else [1.,1.,1.])
            start=time.perf_counter();rgb=render_stack(preset,[light],1080,1920,'cuda',torch.float32).cpu().numpy()
            elapsed=time.perf_counter()-start
            np.save(out/f'{name}-{label}.npy',rgb);save_image(out/f'{name}-{label}.png',rgb+(plate if label=='lantern' else 0))
            meta['items'].append(dict(name=name+'-'+label,preset=raw,preset_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                disabled_source_finish=label=='lantern',light=light,seconds=elapsed,**stats(rgb)))
            (out/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
            print(name,label,round(elapsed,2),'s',flush=True)
    for label in ['black','lantern']:
        sheet=Image.new('RGB',(1536,5*322),(16,18,23));draw=ImageDraw.Draw(sheet)
        for i,r in enumerate(catalog['presets']):
            name=Path(r['file']).stem;im=Image.open(out/f'{name}-{label}.png');im.thumbnail((512,288))
            x=i%3*512;y=i//3*322;sheet.paste(im,(x,y));draw.text((x+7,y+294),r['name'],fill='white')
        sheet.save(out/f'all-15-{label}.jpg',quality=96)
    (out/'PHOTO_CREDITS.md').write_text('Lantern photograph: Jakub T. Jankiewicz, https://commons.wikimedia.org/wiki/File:Lantern_on_Charles_Bridge_at_Night.jpg — https://creativecommons.org/licenses/by-sa/4.0/ . Central crop, resize and additive artistic flare. Derivatives retain CC BY-SA 4.0. No endorsement. QA evidence only, not a packaged preset asset.',encoding='utf-8')


if __name__=='__main__':main()
