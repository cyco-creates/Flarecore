"""Original adaptive looks and visual QA. No third-party media is embedded."""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from flare.schema import validate_preset
from flare.motion import MOTION_TARGETS, MOTION_DRIVERS, sample_curve
from flare.engine import render_stack
from flare.colorspace import linear_to_srgb


def C(target, points, driver='radius'):
    return dict(target=target,driver=driver,points=points,interpolation='smooth')


def E(kind, name, slot, channels=None, **kw):
    e=dict(type=kind,id=name,label=name.replace('_',' '),slot=slot,**kw)
    if channels:
        e['motion']=dict(enabled=True,channels=channels)
    return e


def P(name,category,elements):
    return dict(schema_version=1,name=name,category=category,subcategory='Adaptive Optics',
                author='flarecore',elements=elements,
                **{'global':dict(seed=71,edge_fade_start=.15,edge_fade_range=.9)})


def core():
    return E('glow','source_core','glows',scale=.055,intensity=2.5,params=dict(softness=.32,falloff=2.))


def veil(color, gain=.15):
    return E('glow','source_veil','glows',scale=2.5,intensity=gain,color=color,
             channels=[C('opacity',[[-.7,0],[-.2,.9],[.1,1.35],[.7,.4],[1,.2]],'edge'),
                       C('scale',[[0,.8],[1.3,1],[2.6,1.45]])],
             params=dict(softness=.85,falloff=1.3))


def looks():
    warm=[core(),veil([1,.46,.2],.22),
      E('glow','source_bloom','glows',scale=.35,intensity=.6,color=[1,.7,.38],params=dict(softness=.55,falloff=1.6))]
    for i,(off,size,level) in enumerate([(.55,.04,.24),(.72,.055,.2),(.94,.07,.15),(1.28,.12,.18)]):
        warm.append(E('iris',f'red_ghost_{i+1}','ghosts',offset=off,scale=size,intensity=level,color=[1,.075,.12],blur=.05,
          channels=[C('opacity',[[0,.1],[.65,.65],[1.45,1.2],[2.7,.12]]),C('scale',[[0,.65],[1,1],[2.7,1.6]]),
                    C('stretch_x',[[0,1],[1.4,1.1],[2.7,.6]]),C('rotation',[[-1.5,-14],[0,0],[1.5,14]],'x')],
          params=dict(blades=8,roundness=.55,edge_softness=.25)))
    warm.extend([
      E('iris','large_edge_reflection','ghosts',offset=2.15,scale=.7,intensity=.14,color=[1,.13,.035],irregular=.12,shade=.4,
        channels=[C('opacity',[[0,0],[.5,.1],[1.25,1],[2.4,.6]]),C('scale',[[0,.6],[1.1,1],[2.5,1.65]]),
                  C('crescent',[[0,0],[.8,.05],[1.7,.38],[2.8,.82]]),C('stretch_x',[[0,1],[1.2,.9],[2.6,.6]])],
        params=dict(blades=9,roundness=.8,edge_softness=.16)),
      E('hoop','warm_reflection_rim','hoops',offset=2.1,scale=.72,intensity=.075,color=[1,.4,.05],dispersion=.15,
        channels=[C('opacity',[[0,0],[1,.8],[1.9,1],[2.8,.3]]),C('scale',[[0,.6],[1.1,1],[2.5,1.65]])],
        params=dict(radius=.95,thickness=.035,angular_falloff=.2))])

    cool=[core(),veil([.25,.68,.8],.11),
      E('streak','broken_blue_streak','streaks',scale=1,intensity=.62,color=[.13,.25,1],auto_rotate=False,irregular=.3,
        channels=[C('opacity',[[0,.4],[.65,.85],[1.5,1.25],[2.8,0]]),C('stretch_x',[[0,.6],[1.2,1],[2.8,1.5]]),C('stretch_y',[[0,.65],[1.5,1.2],[2.8,1.8]]),C('curve',[[-1.5,-.14],[0,0],[1.5,.14]],'x')],
        params=dict(length=3,thickness=.006,dash=.4,curve=.07)),
      E('streak','soft_streak_envelope','streaks',scale=1,intensity=.07,color=[.25,.45,1],auto_rotate=False,params=dict(length=2.2,thickness=.024)),
      E('streak','lower_companion','streaks',scale=.8,intensity=.085,color=[.18,.45,1],auto_rotate=False,shift=[0,.06],
        channels=[C('opacity',[[0,0],[.8,.4],[1.65,1],[2.6,0]]),C('shift_y',[[0,0],[1.3,.025],[2.5,.08]])],params=dict(length=2,thickness=.004,dash=.2))]
    for i,off in enumerate([.4,.9,1.45,2.]):
        cool.extend([
          E('glint',f'vertical_ping_{i+1}','rays',offset=off,scale=.22,intensity=.15,color=[.28,.35,1],auto_rotate=False,rotation=90,
            channels=[C('opacity',[[0,.3],[.6+i*.12,1],[1.7+i*.12,.8],[2.8,0]]),C('stretch_x',[[0,.45],[1,1],[2.5,1.45]])],params=dict(points=2,length=.6,thickness=.012,length_jitter=.05)),
          E('iris',f'oval_reflection_{i+1}','ghosts',offset=off,scale=.17,intensity=.024,color=[.25,.52,.8],auto_rotate=False,stretch=[.6,1.5],
            channels=[C('opacity',[[0,.2],[1,1],[2.7,.2]]),C('stretch_x',[[0,1],[1.1,.75],[2.7,.45]]),C('crescent',[[0,0],[1,.15],[2.7,.65]])],params=dict(blades=9,roundness=.8,edge_softness=.25))])

    soft=[core(),veil([1,.88,.58],.65),
      E('glow','wide_soft_bloom','glows',scale=1.2,intensity=.55,color=[1,.98,.9],params=dict(softness=.6,falloff=1.15),
        channels=[C('scale',[[0,.6],[1,1],[2.6,1.6]]),C('opacity',[[-.7,0],[-.1,1.2],[.4,.6],[1,.35]],'edge')]),
      E('spectral','broad_chromatic_arc','rings',offset=.65,scale=1.3,intensity=.12,dispersion=1.1,dispersion_samples=13,irregular=.28,
        channels=[C('scale',[[0,.45],[.8,.8],[1.8,1.25],[2.8,1.65]]),C('opacity',[[0,0],[.7,.4],[1.35,1],[2.7,0]]),C('crescent',[[0,0],[1,.2],[2.8,.8]])],
        params=dict(shape='ring',radius=.95,thickness=.045)),
      E('glint','violet_companion_ray','rays',offset=1.3,scale=.28,intensity=.2,color=[.65,.15,1],auto_rotate=False,rotation=-90,
        channels=[C('opacity',[[0,0],[.9,.4],[1.5,1],[2.5,0]])],params=dict(points=1,length=.8,thickness=.08,length_jitter=0)),
      E('orbs','subtle_lens_surface','ghosts',screen_space=True,screen_blend='all',scale=1.5,intensity=.015,auto_rotate=False,
        params=dict(count=45,size=.014,size_jitter=.7,spread=1.5,edge_softness=.5,illumination=.6))]

    clean=[core(),veil([.55,.6,.85],.045),
      E('glint','soft_directional_fan','rays',scale=.9,intensity=.035,color=[.75,.64,.85],irregular=.4,
        channels=[C('opacity',[[0,.1],[.8,.3],[1.6,1],[2.8,.1]]),C('scale',[[0,.5],[1,1],[2.5,1.35]])],
        params=dict(points=48,length=.8,thickness=.007,length_jitter=.6,completion=90,completion_feather=.8)),
      E('iris','cyan_aperture','ghosts',offset=1.45,scale=.2,intensity=.09,color=[.4,.66,1],
        channels=[C('opacity',[[0,.1],[1,1],[2.7,.3]]),C('roundness',[[0,.8],[1.2,.4],[2.8,.15]]),C('scale',[[0,.6],[1.3,1],[2.8,1.8]])],params=dict(blades=8,roundness=.4,edge_softness=.16)),
      E('glow','violet_companion','glows',offset=1.8,scale=.07,intensity=.28,color=[.65,.25,1],params=dict(softness=.5,falloff=2)),
      E('iris','faint_aperture_chain','ghosts',offset=.7,count=4,spread=.2,scale=.09,count_scale_step=1.1,count_falloff=.8,intensity=.025,color=[.3,.6,.75],
        channels=[C('opacity',[[0,.1],[.8,1],[2.8,.1]]),C('stretch_x',[[0,1],[1.2,.8],[2.8,.5]])],params=dict(blades=8,roundness=.4,edge_softness=.2))]
    return [('adaptive_warm_glass',P('Adaptive - Warm Glass','Spherical',warm)),
            ('adaptive_blue_streak',P('Adaptive - Blue Streak','Anamorphic',cool)),
            ('adaptive_soft_prismatic',P('Adaptive - Soft Prismatic','Scenario',soft)),
            ('adaptive_clean_spherical',P('Adaptive - Clean Spherical','Spherical',clean))]


def main():
    torch.set_num_threads(4)
    dest=ROOT/'docs'/'motion_previews';dest.mkdir(exist_ok=True)
    sheet=Image.new('RGB',(1200,800),'#111820')
    frames=[]
    for row,(filename,raw) in enumerate(looks()):
        p=validate_preset(raw)
        (ROOT/'presets'/f'{filename}.json').write_text(json.dumps(raw,indent=2)+'\n',encoding='utf-8')
        for col,x in enumerate([-1.6,-.5,1.3]):
            rgb=render_stack(p,[dict(x=x,y=-.35)],200,400,torch.device('cpu'),torch.float32)
            img=Image.fromarray((linear_to_srgb(rgb).clamp(0,1).numpy()*255).round().astype(np.uint8))
            ImageDraw.Draw(img).text((10,180),raw['name']+' | x='+str(x),fill='white')
            sheet.paste(img,(400*col,200*row))
    sheet.save(dest/'adaptive_contact_sheet.jpg',quality=94)
    # Forward and reverse sweep exposes jumps, unstable seeds and clipping.
    raw=looks()[1][1];p=validate_preset(raw)
    for x in np.linspace(-2.5,2.5,40):
        rgb=render_stack(p,[dict(x=float(x),y=-.32)],270,480,torch.device('cpu'),torch.float32)
        img=Image.fromarray((linear_to_srgb(rgb).clamp(0,1).numpy()*255).round().astype(np.uint8))
        frames.append(img)
    frames[0].save(dest/'adaptive_sweep.gif',save_all=True,append_images=frames[1:]+frames[-2:0:-1],duration=65,loop=0)
    # Contract and fixtures also support the isolated UI QA page.
    contract=dict(targets=MOTION_TARGETS,drivers=MOTION_DRIVERS)
    (dest/'motion_contract.json').write_text(json.dumps(contract,indent=2)+'\n',encoding='utf-8')
    points=[[0,.1],[.4,1.7],[1,.3],[2,1.2]]
    cases=[dict(points=points,x=x,mode=mode,expected=sample_curve(points,x,mode)) for mode in ('smooth','linear') for x in [-1,0,.01,.2,.4,.8,1,1.5,2,3]]
    (dest/'curve_fixtures.json').write_text(json.dumps(cases)+'\n',encoding='utf-8')
    print('Built four adaptive presets, contact sheet, motion sweep and UI fixtures.')


if __name__=='__main__':
    main()
