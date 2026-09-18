"""Rebuild the eight procedural realism textures and their demonstration looks.

These are original engine renders, not measured lens responses. Run from any
directory with the ComfyUI Python interpreter. The AI texture is a separate
authored asset and is never overwritten by this script.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flare.schema import validate_preset
from flare.engine import render_stack
from flare.colorspace import linear_to_srgb
from flare.texture_prep import feather_border


def element(kind, name, slot, **kwargs):
    return dict(type=kind, id=name, label=name.replace('_', ' '), slot=slot,
                **kwargs)


RECIPES = [
    element('iris', 'rounded_nine_blade', 'ghosts', scale=.58,
            color=[.68,.82,1], intensity=.28, irregular=.12,
            params=dict(blades=9, roundness=.65, edge_softness=.12)),
    element('iris', 'barrel_clipped_amber', 'ghosts', scale=.58,
            color=[1,.68,.36], intensity=.3, irregular=.16, shade=.45,
            params=dict(blades=7, roundness=.4, crescent=.58, crescent_feather=.16)),
    element('iris', 'soft_aperture_rim', 'ghosts', scale=.58,
            color=[.58,.9,.78], intensity=.32, dispersion=.12,
            params=dict(blades=6, roundness=.3, hollow=.86, edge_softness=.1)),
    element('glow', 'neutral_scatter_halo', 'glows', scale=.38,
            color=[1,.95,.85], intensity=.8, irregular=.16,
            params=dict(softness=.35, falloff=1.85)),
    element('streak', 'fine_bowed_streak', 'streaks', scale=.25,
            color=[.45,.65,1], intensity=.65, irregular=.2, auto_rotate=False,
            params=dict(length=1, thickness=.009, curve=.16)),
    element('glint', 'single_soft_ray', 'rays', scale=.3,
            color=[1,.88,.7], intensity=.65, auto_rotate=False,
            params=dict(points=1, length=.55, thickness=.01, length_jitter=0)),
    element('ring', 'broken_coating_arc', 'rings', scale=.65,
            color=[.6,.78,1], intensity=.4, irregular=.3, dispersion=.2,
            dispersion_samples=7,
            params=dict(radius=.72, thickness=.022, completion=235, completion_feather=.4)),
    element('hoop', 'soft_asymmetric_hoop', 'hoops', scale=.62,
            color=[1,.73,.5], intensity=.3, irregular=.25,
            params=dict(radius=.65, thickness=.14, angular_falloff=.7, crescent=.45)),
]


def preset(name, category, elems):
    return dict(schema_version=1, name=name, author='flarecore', category=category,
                subcategory='Realism Studies',
                **{'global': dict(seed=27, edge_fade_start=.05, edge_fade_range=.6)},
                elements=elems)


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def preview(raw, h, w, light=None):
    p = validate_preset(raw)
    for e in p['elements']:
        if e['type'] == 'texture':
            from flare.colorspace import srgb_to_linear
            with Image.open(ROOT/'elements'/e['params']['file']) as img:
                tex = torch.from_numpy(np.asarray(img.convert('RGB')).copy()).float()/255
            e['params']['_texture'] = srgb_to_linear(tex)
    return render_stack(p, [light or dict(x=0., y=0., brightness=1., occlusion=0.)],
                        h, w, torch.device('cpu'), torch.float32)


def main():
    torch.set_num_threads(4)
    output = ROOT/'docs'/'realism_previews'
    output.mkdir(exist_ok=True)
    tiles = []
    for e in RECIPES:
        raw = preset(e['label'], 'Utility', [e])
        rgb = preview(raw, 1024, 1024)
        encoded = feather_border(linear_to_srgb(rgb).clamp(0, 1), .12)
        img = Image.fromarray((encoded.numpy()*255).round().astype(np.uint8))
        img.save(ROOT/'elements'/e['slot']/('realism_'+e['id']+'.png'))
        tile = Image.new('RGB', (280, 306), '#111318')
        tile.paste(img.resize((280,280)), (0,0))
        ImageDraw.Draw(tile).text((8,286), e['label'], fill='white')
        tiles.append(tile)
    sheet = Image.new('RGB', (1120,612), '#111318')
    for i,t in enumerate(tiles):
        sheet.paste(t, ((i%4)*280,(i//4)*306))
    sheet.save(output/'elements.jpg', quality=94)

    core = element('glow', 'source_core', 'glows', scale=.055, intensity=2.3,
                   params=dict(softness=.3, falloff=2.2))
    halo = element('glow', 'source_scatter', 'glows', scale=.28, intensity=.12,
                   color=[1,.91,.77], params=dict(softness=.4, falloff=1.8))
    spherical = preset('Realism - Coated Spherical', 'Spherical', [core, halo,
        element('iris','rounded_ghost_chain','ghosts', offset=.65, count=3, spread=.48,
                scale=.08, count_scale_step=1.48, count_falloff=.72, intensity=.055,
                color=[.55,.8,.63], dispersion=.1, irregular=.12,
                params=dict(blades=9,roundness=.6,edge_softness=.2)),
        element('texture','coated_aperture','ghosts',offset=1.8,scale=.45,intensity=.1,
                params=dict(file='ghosts/realism_coated_aperture.png',channel='rgb'))])
    anamorphic = preset('Realism - Restrained Anamorphic', 'Anamorphic', [core, halo,
        element('streak','bowed_blue_line','streaks', scale=1.,intensity=.16,
                color=[.2,.47,1],auto_rotate=False,irregular=.1,
                params=dict(length=1.7,thickness=.003,curve=.07)),
        element('streak','companion_line','streaks', scale=.8,intensity=.035,
                shift=[0,.08],color=[.45,.6,1],auto_rotate=False,
                params=dict(length=1.1,thickness=.005)),
        element('iris','clipped_oval','ghosts', offset=1.6,scale=.18,intensity=.04,
                stretch=[.65,1.3],color=[.65,.85,.7],irregular=.12,
                params=dict(blades=8,roundness=.75,crescent=.35,edge_softness=.2))])
    stopped = preset('Realism - Stopped Down Warm', 'Spherical', [core, halo,
        element('glint','six_blade_star','rays',scale=.35,intensity=.14,
                auto_rotate=False,rotation=15,color=[1,.88,.68],irregular=.08,
                params=dict(points=6,length=.6,thickness=.004,length_jitter=.08)),
        element('iris','six_blade_ghosts','ghosts', offset=.8,count=3,spread=.5,
                scale=.06,count_scale_step=1.5,count_falloff=.7,intensity=.045,
                color=[.55,.78,1],params=dict(blades=6,roundness=.08,edge_softness=.12))])
    looks = [('realism_coated_spherical',spherical),
             ('realism_restrained_anamorphic',anamorphic),
             ('realism_stopped_down_warm',stopped)]
    shots = Image.new('RGB',(960,600),'#101318')
    for row,(filename,raw) in enumerate(looks):
        validate_preset(raw)
        save_json(ROOT/'presets'/(filename+'.json'),raw)
        for col,light in enumerate([dict(x=-1.,y=-.4),dict(x=.3,y=.1),dict(x=1.6,y=-.7)]):
            rgb = preview(raw,180,320,light)
            # A neutral linear plate makes weak veiling and ghost levels visible.
            rgb = linear_to_srgb(rgb+.018).clamp(0,1)
            img = Image.fromarray((rgb.numpy()*255).round().astype(np.uint8))
            shots.paste(img,(col*320,row*200))
        ImageDraw.Draw(shots).text((8,row*200+184),raw['name'],fill='white')
    shots.save(output/'presets.jpg',quality=94)
    save_json(ROOT/'prompts'/'realism_recipes.json', RECIPES)
    print('Built 8 textures, 3 presets and 2 contact sheets.')


if __name__ == '__main__':
    main()
