import copy
import json
from pathlib import Path

import pytest
import torch

from flare.elements import glint, iris, glow, ring, _surface_detail
from flare.engine import render_stack
from flare.motion import apply_motion
from flare.schema import validate_preset, PARAM_DEFAULTS
from scripts.build_reference_studies import build, vertical_axis, line
from conftest import load_package
resolve_textures=load_package().nodes.library.resolve_preset_textures

ROOT=Path(__file__).resolve().parents[1]


def test_collection_is_reproducible_and_fully_labelled():
    studies=build()
    assert len(studies)==15
    assert len({s['key'] for s in studies})==15
    for s in studies:
        path=ROOT/'presets'/f'study_{s["key"]}.json'
        assert json.loads(path.read_text(encoding='utf-8'))==s['preset']
        assert s['reference']['focal_mm']>0 and s['reference']['t_stop']>0
        p=validate_preset(s['preset'])
        assert not p.get('lens_lab',{}).get('enabled',False)
        assert len({e['id'] for e in p['elements']})==len(p['elements'])
        for e in p['elements']:
            if e['type']=='texture':assert (ROOT/'elements'/e['params']['file']).is_file()
        assert any(e.get('motion') for e in p['elements'])


@pytest.mark.parametrize('s',build(),ids=lambda s:s['key'])
def test_study_is_finite_reversible_and_does_not_mutate(s):
    p=validate_preset(s['preset'])
    resolve_textures(p,device='cpu',dtype=torch.float32)
    before=copy.deepcopy(p)
    for height,width in [(65,97),(97,65)]:
        lights=[dict(x=-.7,y=-.3),dict(x=.3,y=.15),dict(x=-.7,y=-.3)]
        images=[render_stack(p,[l],height,width,'cpu',torch.float32) for l in lights]
        assert all(torch.isfinite(im).all() and im.min()>=0 for im in images)
        torch.testing.assert_close(images[0],images[2],rtol=0,atol=0)
    for e,b in zip(p['elements'],before['elements']):
        if '_texture' in e['params']:
            torch.testing.assert_close(e['params']['_texture'],b['params']['_texture'],rtol=0,atol=0)
            e['params'].pop('_texture');b['params'].pop('_texture')
    assert p==before


def test_anamorphic_y_follows_source_instead_of_reference_capture_row():
    e=vertical_axis(line('test',.6,.7,.1,[0,0,1]),0)
    e=validate_preset(dict(schema_version=1,elements=[e]))['elements'][0]
    for y in [-1.2,-.3,0,.4,1.2]:
        moved,_=apply_motion(e,dict(x=.5,y=y),16/9)
        assert moved['shift'][1]==pytest.approx(y)


def test_legacy_ray_controls_are_exact_noops():
    u,v=torch.meshgrid(torch.linspace(-2,2,73),torch.linspace(-1,1,49),indexing='ij')
    p=dict(PARAM_DEFAULTS['glint'],seed=27)
    old={k:v for k,v in p.items() if k not in ('ray_falloff','fan','ray_taper')}
    torch.testing.assert_close(glint(u,v,p),glint(u,v,old),rtol=0,atol=0)


def test_compact_ray_tails_and_surface_detail_are_bounded():
    u=torch.tensor([[3.]])
    v=torch.zeros_like(u)
    p=dict(PARAM_DEFAULTS['glint'],points=1,length=1,length_jitter=0)
    assert glint(u,v,dict(p,ray_falloff=2)).item()<glint(u,v,p).item()/100
    u,v=torch.meshgrid(torch.linspace(-1,1,63),torch.linspace(-1,1,65),indexing='ij')
    field=torch.ones_like(u)
    torch.testing.assert_close(_surface_detail(field,u,v,{}),field,rtol=0,atol=0)
    detail=_surface_detail(field,u,v,dict(surface_detail=1,seed=27,_px=.01))
    assert detail.min()>=0 and detail.max()<=2 and detail.std()>.02
    tiny=_surface_detail(field,u,v,dict(surface_detail=1,seed=27,_px=1))
    assert tiny.std()<detail.std()*.01


@pytest.mark.parametrize('param,value',[('fan',-.1),('fan',.6),('ray_falloff',.9),('ray_falloff',5),('ray_falloff',float('nan'))])
def test_invalid_source_shape_controls_rejected(param,value):
    with pytest.raises(ValueError,match=param):
        validate_preset(dict(schema_version=1,elements=[dict(type='glint',params={param:value})]))


def test_tiny_half_precision_surface_coordinates_do_not_overflow():
    u=torch.tensor([[30000.,-30000.]],dtype=torch.float16)
    v=torch.tensor([[20000.,-20000.]],dtype=torch.float16)
    result=_surface_detail(torch.ones_like(u),u,v,dict(surface_detail=1,_px=.01,seed=1))
    assert result.dtype==torch.float16 and torch.isfinite(result).all()


def test_density_bias_is_bounded_and_legacy_is_unchanged():
    u=torch.linspace(0,1,101)[None];v=torch.zeros_like(u)
    p=dict(PARAM_DEFAULTS['iris'],roundness=1)
    legacy={k:v for k,v in p.items() if k!='density_bias'}
    base=iris(u,v,legacy)
    torch.testing.assert_close(iris(u,v,p),base,rtol=0,atol=0)
    for bias in [-1,-.5,.5,1]:
        result=iris(u,v,dict(p,density_bias=bias))
        assert torch.isfinite(result).all() and result.min()>=0
        assert (result<=base+1e-7).all()
    assert iris(u,v,dict(p,density_bias=1))[0,0]==0


@pytest.mark.parametrize('kind,param,value',[('iris','density_bias',1.1),('iris','density_bias',float('nan')),('glint','ray_taper',-1),('glint','ray_taper',1.1)])
def test_extra_shape_controls_validate(kind,param,value):
    with pytest.raises(ValueError,match=param):
        validate_preset(dict(schema_version=1,elements=[dict(type=kind,params={param:value})]))


def test_fit_preserves_authored_hues_and_source_finish():
    fitted_sources=json.loads((ROOT/'docs/reference_study_fits.json').read_text(encoding='utf-8')).get('fit_source',False)
    for raw,fitted in zip(build(False),build(True)):
        for a,b in zip(raw['preset']['elements'],fitted['preset']['elements']):
            if a['label'].startswith('Source ·') and not fitted_sources:
                assert b['color']==a['color']
            ratios=[c/d for c,d in zip(b['color'],a['color']) if d>0]
            assert max(ratios)-min(ratios)<.001


@pytest.mark.parametrize('kind,function,keys',[
    ('glow',glow,['scatter']),('iris',iris,['coma','caustic']),('ring',ring,['edge_bias'])])
def test_new_transport_controls_preserve_legacy_exactly(kind,function,keys):
    u,v=torch.meshgrid(torch.linspace(-2,2,63),torch.linspace(-2,2,65),indexing='ij')
    p=dict(PARAM_DEFAULTS[kind],seed=82)
    old={k:v for k,v in p.items() if k not in keys}
    torch.testing.assert_close(function(u,v,p),function(u,v,old),rtol=0,atol=0)


def test_sheared_pupil_does_not_repeat_outside_its_footprint():
    u=torch.tensor([[-100.,-20.,20.,100.]],dtype=torch.float32);v=torch.zeros_like(u)
    for coma in [-1,-.75,.75,1]:
        p=dict(PARAM_DEFAULTS['iris'],coma=coma)
        assert iris(u,v,p).max()==0


@pytest.mark.parametrize('kind,param,bad',[
    ('iris','coma',1.01),('iris','coma',float('nan')),('iris','caustic',-1),
    ('glow','scatter',float('inf')),('glow','scatter',1.01),('ring','edge_bias',-.41)])
def test_transport_params_reject_invalid_values(kind,param,bad):
    with pytest.raises(ValueError,match=param):
        validate_preset(dict(schema_version=1,elements=[dict(type=kind,params={param:bad})]))


def test_pupil_mapping_is_repeatable_finite_and_has_concentrated_folds():
    from flare.pupil import pupil_caustic
    u,v=torch.meshgrid(torch.linspace(-1.5,1.5,161),torch.linspace(-1.5,1.5,159),indexing='ij')
    p=dict(PARAM_DEFAULTS['iris'],caustic=.9,coma=.4,roundness=.95,seed=54)
    a=pupil_caustic(u,v,p);b=pupil_caustic(u,v,p)
    torch.testing.assert_close(a,b,rtol=0,atol=0)
    assert torch.isfinite(a).all() and a.min()>=0 and a.max()>a.mean()*15
    # The controlled finite source filter keeps a folded pupil from becoming
    # an infinitely bright singularity; there is no per-frame max normalization.
    assert a.max()<200
    assert pupil_caustic(torch.full((1,2),30000.,dtype=torch.float16),
                         torch.zeros((1,2),dtype=torch.float16),p).max()==0


def test_scatter_filters_tiny_angular_features_without_temporal_noise():
    u,v=torch.meshgrid(torch.linspace(-1,1,93),torch.linspace(-1,1,91),indexing='ij')
    p=dict(PARAM_DEFAULTS['glow'],scatter=1,seed=5,_px=.01)
    a=glow(u,v,p);b=glow(u,v,p)
    assert torch.isfinite(a).all() and a.min()>=0
    torch.testing.assert_close(a,b,rtol=0,atol=0)
