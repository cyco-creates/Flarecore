import copy
import json
import math
from pathlib import Path

import pytest
import torch

from flare.motion import sample_curve, apply_motion, driver_value, MOTION_TARGETS
from flare.schema import validate_preset
from flare.engine import render_stack, render_batch


def preset(channels=None, **kwargs):
    e=dict(type='iris', id='stable', scale=.24, params=dict(blades=7), **kwargs)
    if channels is not None:
        e['motion']=dict(enabled=True,channels=channels)
    return validate_preset(dict(schema_version=1,elements=[e]))


def curve(target='opacity',driver='radius',points=None):
    return dict(target=target,driver=driver,points=points or [[0,0],[1,1],[2,0]],interpolation='smooth')


def render(p, lights):
    return render_stack(p,lights,48,80,torch.device('cpu'),torch.float32)


def test_bounded_curve_endpoints_and_no_overshoot():
    points=[[0,0],[.2,2],[.5,.3],[1,1]]
    for mode in ('linear','smooth'):
        assert sample_curve(points,-1,mode)==0
        assert sample_curve(points,2,mode)==1
        for a,b in zip(points,points[1:]):
            values=[sample_curve(points,a[0]+(b[0]-a[0])*i/100,mode) for i in range(101)]
            assert min(values)>=min(a[1],b[1])-1e-12
            assert max(values)<=max(a[1],b[1])+1e-12


@pytest.mark.parametrize('bad', [
    [curve(points=[[0,1],[0,2]])], [curve(points=[[1,1],[0,2]])],
    [curve(points=[[0,1],[1,float('nan')]])], [curve(points=[[0,1],[1,9]])],
    [curve(),curve()], [curve(target='unknown')], [curve(driver='unknown')],
    [dict(curve(), points=[[0,1]])], [dict(curve(), interpolation='spline')],
    [curve(points=[[False,1],[1,1]])],
])
def test_invalid_curves_rejected(bad):
    with pytest.raises(ValueError,match='motion'):
        preset(bad)


def test_shape_channels_reject_wrong_element_type():
    with pytest.raises(ValueError,match='not supported'):
        preset([curve(target='curve')])


def test_neutral_motion_is_pixel_identical_and_presets_unmodified():
    static=preset()
    dynamic=preset([curve(target=t,points=[[0,1],[2,1]]) for t in ('opacity','scale','stretch_x','stretch_y','color_r')])
    before=copy.deepcopy(dynamic)
    lights=[dict(x=.6,y=.25,brightness=1,occlusion=0)]
    torch.testing.assert_close(render(static,lights),render(dynamic,lights),rtol=0,atol=0)
    assert dynamic==before
    dynamic['elements'][0]['motion']['enabled']=False
    torch.testing.assert_close(render(static,lights),render(dynamic,lights),rtol=0,atol=0)


def test_opacity_zero_suppresses_additive_legacy_trigger():
    p=preset([curve(points=[[0,0],[2,0]])],trigger=dict(mode='center',brightness=4))
    assert render(p,[dict(x=0,y=0)]).max()==0


def test_channels_act_independently():
    p=preset([curve('scale',points=[[0,1],[2,2]]),curve('stretch_x','x',[[-1,.5],[1,1.5]]),curve('rotation','y',[[-1,-30],[1,30]]),curve('crescent',points=[[0,0],[2,.8]])])
    e,opacity=apply_motion(p['elements'][0],dict(x=0,y=1),2)
    assert opacity==1
    assert e['scale']==pytest.approx(.36)
    assert e['stretch']==[1,1]
    assert e['rotation']==30
    assert e['params']['crescent']==pytest.approx(.4)


def test_real_source_and_aspect_units_used_for_lens_plates():
    light=dict(x=0,y=0,lx=2,ly=.5,source_brightness=3,source_occlusion=.4,occlusion=0)
    assert driver_value('x',light,2)==1
    assert driver_value('radius',light,2)==math.hypot(2,.5)
    assert driver_value('edge',light,2)==0
    assert driver_value('brightness',light,2)==3
    assert driver_value('occlusion',light,2)==.4


def test_motion_reversible_and_chunk_invariant():
    p=preset([curve(),curve('rotation','x',[[-1,-25],[1,25]]),curve('stretch_y',points=[[0,.8],[2,1.6]])])
    path=[[dict(x=x,y=.1)] for x in [-1.3,-.8,-.3,.3,.8,1.3]]
    args=(48,80,torch.device('cpu'),torch.float32)
    whole=render_batch(p,path,*args)
    reverse=render_batch(p,list(reversed(path)),*args).flip(0)
    chunks=torch.cat([render_batch(p,path[:2],*args),render_batch(p,path[2:],*args)])
    torch.testing.assert_close(whole,reverse,rtol=0,atol=0)
    torch.testing.assert_close(whole,chunks,rtol=0,atol=0)


def test_all_light_lens_surface_equals_sum_of_single_sources():
    p=preset(screen_space=True,screen_blend='all')
    p['elements'][0]['motion']={'enabled':True,'channels':[curve('color_r','x',[[-1,.5],[1,1.5]])]}
    lights=[dict(x=-.8,y=.2,brightness=.6,color=[1,.3,.2]),dict(x=.7,y=-.2,brightness=.9,color=[.2,.4,1])]
    torch.testing.assert_close(render(p,lights),render(p,[lights[0]])+render(p,[lights[1]]),rtol=1e-6,atol=1e-7)


def test_all_light_response_is_continuous_at_brightness_crossover():
    p=preset(screen_space=True,screen_blend='all')
    def at(delta):
        return render(p,[dict(x=-.5,y=0,brightness=1+delta,color=[1,0,0]),dict(x=.5,y=0,brightness=1-delta,color=[0,0,1])])
    assert (at(.0001)-at(-.0001)).abs().max()<.001


def test_every_target_produces_finite_nonnegative_render():
    for target,spec in MOTION_TARGETS.items():
        kind=spec.get('types',['iris'])[0]
        e=dict(type=kind,scale=.25,motion=dict(channels=[curve(target,points=[[0,spec['min']],[2,spec['max']]])]))
        p=validate_preset(dict(schema_version=1,elements=[e]))
        out=render(p,[dict(x=.8,y=.4)])
        assert torch.isfinite(out).all(),target
        assert out.min()>=0,target
