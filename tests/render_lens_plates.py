# SPDX-License-Identifier: Apache-2.0
"""Opt-in photographic QA. Photographs are external test data, never packaged.

Usage: python tests/render_lens_plates.py --assets DIR --output DIR
All photographic derivatives retain the source photograph's applicable license.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import torch
from PIL import Image, ImageOps
from render_lens_candidate import Evidence, save_image, stats
from flare.lens_lab import validate_settings
from flare.colorspace import srgb_to_linear


SOURCES = {
 'sunset':dict(file='plate-sunset-original.jpg',author='Prosthetic Head',
               source='https://commons.wikimedia.org/wiki/File:Field_Sunset.jpg',
               license='https://creativecommons.org/licenses/by-sa/4.0/',u=.904,v=.398,color=[1.,.67,.34]),
 'lantern':dict(file='plate-lantern-original.jpg',author='Jakub T. Jankiewicz',
                source='https://commons.wikimedia.org/wiki/File:Lantern_on_Charles_Bridge_at_Night.jpg',
                license='https://creativecommons.org/licenses/by-sa/4.0/',u=.344,v=.289,color=[1.,.62,.28]),
 'tree-edge':dict(file='plate-test.png',author='User project footage; original author/capture origin unverified',
                 source='ComfyUI/input/test.mp4, frame at 2 seconds',license='User-supplied project asset; QA use only',
                 u=.795,v=.13,color=[1.,.91,.74]),
}


def main():
    p=argparse.ArgumentParser();p.add_argument('--assets',required=True);p.add_argument('--output',required=True)
    p.add_argument('--photographed-sources',action='store_true',help='Avoid adding a second bright core to a pre-bloomed plate')
    args=p.parse_args(); torch.set_num_threads(1)
    e=Evidence(args.output,'cuda');e.meta['photographs']={}
    e.render('beauty-on-black',{},h=1080,w=1920)
    e.render('trace-on-black',dict(source_glow=0,source_rays=0),h=1080,w=1920)
    for name,s in SOURCES.items():
        file=Path(args.assets)/s['file']
        image=Image.open(file).convert('RGB')
        # Declared central crop and conventional photographic resampling. Optical
        # convergence references use a separate explicit linear-light reduction.
        photo=ImageOps.fit(image,(1920,1080),method=Image.Resampling.LANCZOS)
        plate=srgb_to_linear(torch.from_numpy(np.asarray(photo).copy()).float()/255).numpy()
        np.save(e.folder/(name+'-plate.npy'),plate);save_image(e.folder/(name+'-plate.png'),plate)
        e.meta['photographs'][name]=dict(s,sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                                       original_size=image.size,processing='central 16:9 crop; Lanczos resize in sRGB; decode sRGB to linear; additive lens pass; clip only for PNG display')
        for label,ev,strength in [('restrained',6.,.65),('strong',6.,2.6)]:
            finish=dict(source_size=.011,source_rays=.5)
            if args.photographed_sources:
                finish.update(source_glow=.12 if name=='sunset' else 0.,source_rays=.15 if name=='sunset' else 0.)
            cfg=validate_settings(dict(exposure=ev,**finish))
            lights=[dict(u=s['u'],v=s['v'],color=s['color'],brightness=strength,occlusion=0.)]
            e.render(name+'-'+label,cfg,lights,h=1080,w=1920,plate=plate)
        if name=='lantern':
            # Second deliberately added practical source is labelled, not claimed
            # to be present in the original photograph.
            lights=[dict(u=s['u'],v=s['v'],color=s['color'],brightness=.65),
                    dict(u=.87,v=.73,color=[.45,.7,1.],brightness=.18)]
            e.render('two-source-study',dict(source_size=.011,source_glow=0. if args.photographed_sources else .65,source_rays=0. if args.photographed_sources else .5),lights,h=1080,w=1920,plate=plate)
    e.flush()
    (e.folder/'PHOTO_CREDITS.md').write_text('# Photographic QA credits\n\n'+
       '\n\n'.join(f"{k}: {v['author']}. Source: {v['source']}. License: {v['license']}. Changes: central crop, resize, and added Flarecore light in composites; original plate supplied separately. No endorsement implied." for k,v in SOURCES.items())+
       '\n\nThe two-source study includes a deliberately added cool secondary source. These photos are test evidence, not distributed Flarecore assets.\n',encoding='utf-8')
    e.sheet([f'{name}-{level}-composite' for name in SOURCES for level in ['restrained','strong']],filename='plate-contact.png',cols=2)


if __name__=='__main__':main()
