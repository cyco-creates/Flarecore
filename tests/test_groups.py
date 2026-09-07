# SPDX-License-Identifier: Apache-2.0
import copy
import json
import pytest
import torch
from test_nodes import run_node, PRESET, FlareRender
from flare.colorspace import srgb_to_linear,linear_to_srgb

def group(ident, x=.3, **global_values):
    p=json.loads(PRESET);p['global']=global_values
    return dict(id=ident,name=ident,enabled=True,preset=p,
                source=dict(position_mode='manual',light_x=x,light_y=.4,visibility_mode='off'))


def scene(groups):
    return json.dumps(dict(schema_version=1,elements=[],global_={},groups=groups,active_group=groups[0]['id']))


def render(image, groups, **kwargs):
    return run_node(image,preset_json=scene(groups),**kwargs)


def test_groups_equal_separate_linear_passes_and_composite_once():
    image=torch.full((2,48,80,3),.1)
    a,b=group('a',.2,master=.7,aspect=1.8),group('b',.8,master=1.2,aspect=.6)
    out,passed,_=render(image,[a,b],colorspace='linear',clamp_output=False)
    pa=run_node(image,preset_json=json.dumps(a['preset']),colorspace='linear',clamp_output=False,**a['source'])[1]
    pb=run_node(image,preset_json=json.dumps(b['preset']),colorspace='linear',clamp_output=False,**b['source'])[1]
    assert torch.allclose(passed,pa+pb,atol=1e-6)
    assert torch.allclose(out,image+pa+pb,atol=1e-6)


def test_srgb_groups_preserve_hdr_until_final_encode():
    image=torch.full((1,32,48,3),.2);a=group('a',master=2)
    passed=render(image,[a,group('b',master=2)],clamp_output=False)[1]
    single=run_node(image,preset_json=json.dumps(a['preset']),clamp_output=False,**a['source'])[1]
    assert torch.allclose(passed,linear_to_srgb(2*srgb_to_linear(single)),atol=2e-5)


def test_group_order_does_not_change_source_detection():
    image=torch.zeros(3,48,80,3);image[:,10:14,15:19]=1
    a,b=group('a'),group('b',.8)
    a['source'].update(position_mode='detect',detect_threshold=.6,scene_lock=0)
    ab=render(image,[a,b])[1];ba=render(image,[b,a])[1]
    assert torch.allclose(ab,ba,atol=1e-6)


def test_group_depth_settings_are_independent():
    image=torch.zeros(1,48,80,3);depth=torch.zeros_like(image);depth[:,:,:40]=1
    a,b=group('a',.2),group('b',.8)
    a['source'].update(visibility_mode='depth',light_depth=0,occlusion_radius=.02)
    b['source'].update(a['source']);b['source']['light_x']=.8
    passed=render(image,[a,b],depth=depth)[1]
    b_only=render(image,[b],depth=depth)[1]
    assert torch.allclose(passed,b_only)


def test_disabled_groups_leave_original_untouched():
    image=torch.rand(2,32,48,3);a=group('a');a['enabled']=False
    out,passed,mask=render(image,[a])
    assert torch.allclose(out,image,atol=1e-6)
    assert passed.count_nonzero()==mask.count_nonzero()==0


def test_group_input_is_not_mutated_and_chunking_is_invariant():
    image=torch.rand(3,32,48,3);groups=[group('a'),group('b',.8,master=.2)];original=copy.deepcopy(groups)
    assert torch.allclose(render(image,groups,chunk_frames=1)[1],render(image,groups,chunk_frames=3)[1])
    assert groups==original


def test_external_lights_are_opt_in_per_group():
    image=torch.zeros(1,32,48,3);a=group('a',.2);b=group('b',.8)
    external=[[dict(u=.5,v=.5,brightness=1.)]]
    plain=render(image,[a,b])[1]
    assert torch.allclose(plain,render(image,[a,b],lights=external)[1])
    a['source']['use_lights_input']=True
    assert not torch.allclose(plain,render(image,[a,b],lights=external)[1])


def test_group_paths_are_independent_across_video():
    image=torch.zeros(4,32,48,3);a=group('a');b=group('b')
    a['source'].update(position_mode='path',light_path='.1,.2; .8,.2')
    b['source'].update(position_mode='path',light_path='.8,.7; .2,.7')
    both=render(image,[a,b],colorspace='linear',clamp_output=False)[1]
    one=render(image,[a],colorspace='linear',clamp_output=False)[1]
    two=render(image,[b],colorspace='linear',clamp_output=False)[1]
    assert torch.allclose(both,one+two)


def test_group_ui_results_are_keyed_by_group_not_shared(monkeypatch):
    from test_nodes import PKG
    monkeypatch.setattr(PKG.nodes.render,'_folder_paths',object())
    monkeypatch.setattr(PKG.nodes.render,'_save_preview',lambda frame: {'images':[]})
    # Required render arguments are the same ones used by the test helper.
    args=dict(preset_json=scene([group('a',.2),group('b',.8)]),position_mode='manual',
       light_x=.3,light_y=.4,flare_x=.5,flare_y=.5,detect_threshold=.8,detect_max_lights=1,
       occlusion_radius=.02,light_depth=0,invert_depth=False,intensity=1,scale=1,
       blend_mode='add',clamp_output=True,seed=0)
    result=FlareRender().render(image=torch.zeros(1,24,32,3),**args)
    groups=result['ui']['fc_groups'][0]
    assert groups['a']['track'][0][0]==.2
    assert groups['b']['track'][0][0]==.8
    assert result['ui']['fc_track'][0]==groups['a']['track']


@pytest.mark.parametrize('mutation',[
    lambda gs:gs.append(copy.deepcopy(gs[0])),
    lambda gs:gs[0]['source'].update(light_x=float('nan')),
    lambda gs:gs[0]['source'].update(preset_json='bad'),
    lambda gs:gs[0]['source'].update(position_mode='wrong'),
    lambda gs:gs[0]['preset'].update(groups=[]),
    lambda gs:gs[0].update(enabled='false'),
])
def test_invalid_groups_fail_clearly(mutation):
    gs=[group('a')];mutation(gs)
    with pytest.raises(ValueError):render(torch.zeros(1,16,16,3),gs)
