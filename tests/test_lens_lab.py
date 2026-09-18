# SPDX-License-Identifier: Apache-2.0
import copy
import json
import math

import pytest
import torch

from flare.lens_lab import (MODEL_ID, _itinerary, aperture_mask, diagram, fresnel,
                            ghost_pairs, intersect_surface, model_for, render_lens,
                            trace, validate_settings, _reconstruct)
from flare.schema import validate_preset, load_preset
from flare.engine import render_stack, render_batch


def settings(**kw):
    # A subset for fast regression renders; full 66 paths tested separately.
    keep = {(0, 1), (0, 2), (1, 2), (6, 7), (8, 9), (11, 12)}
    return validate_settings(dict(quality='draft', disabled_pairs=[f'{a}:{b}' for a, b in ghost_pairs() if (a, b) not in keep], **kw))


def preset(config=None, elements=None):
    return validate_preset(dict(schema_version=1, elements=elements or [], lens_lab=config or settings()))


LIGHT = dict(x=.4, y=-.2, u=.7, v=.4, brightness=1.)


def test_prescription_and_reflection_order():
    model = model_for(settings())
    assert model['focal_length'] == pytest.approx(22.023496, abs=1e-5)
    assert model['sensor_z'] == pytest.approx(47.689403, abs=1e-5)
    assert len(ghost_pairs()) == 66
    assert all(a != 5 and b != 5 and a < b for a, b in ghost_pairs())
    itinerary = _itinerary((1, 3), 6)
    assert [s for s, _, _ in itinerary] == [0, 1, 2, 3, 2, 1, 2, 3, 4, 5]
    assert [s for s, reflect, _ in itinerary if reflect] == [3, 1]


def test_normal_fresnel_and_total_internal_reflection():
    r, c, passed = fresnel(torch.tensor([1., .4]), torch.tensor([1., 1.5]), torch.tensor([1.5, 1.]))
    assert r.tolist() == pytest.approx([.04, 1.])
    assert passed.tolist() == [True, False]


@pytest.mark.parametrize('radius', [10., -10., 0.])
def test_surface_vertex_hemisphere_both_directions(radius):
    for zstart, dz in [(-2., 1.), (2., -1.)]:
        origin = torch.tensor([[0., 0., zstart]], dtype=torch.float64)
        direction = torch.tensor([[0., 0., dz]], dtype=torch.float64)
        hit, normal, valid = intersect_surface(origin, direction, torch.tensor([0.]), torch.tensor([radius]))
        assert valid.item()
        assert hit.norm() < 1e-8
        assert (normal * direction).sum() <= 0


def test_direct_ray_focus_and_rotational_symmetry():
    config = settings(blades=0)
    model = model_for(config)
    pupil = torch.tensor([[.001, 0], [-.001, 0], [0, .001], [0, -.001]], dtype=torch.float64)
    pos, energy, _ = trace(model, config, .5, .5, 16/9, pupil, [None])
    assert energy.min() > 0
    assert pos[..., :2].abs().max() < 1e-7
    p1, e1, _ = trace(model, config, .6, .4, 16/9, pupil, [(6, 7)])
    p2, e2, _ = trace(model, config, .4, .6, 16/9, -pupil, [(6, 7)])
    assert torch.allclose(e1, e2, atol=1e-10)
    assert torch.allclose(p1[..., :2], -p2[..., :2], atol=1e-8)


def test_polygon_stop_and_fnumber():
    x = torch.tensor([0., .9, 1.1]); y = torch.zeros_like(x); radius = torch.ones_like(x)
    assert aperture_mask(x, y, radius, 0, 0).tolist() == [True, True, False]
    assert aperture_mask(x, y, radius, 4, 0).tolist() == [True, False, False]
    assert model_for(settings(f_stop=8))['surfaces'][5]['aperture'] == pytest.approx(model_for(settings(f_stop=4))['surfaces'][5]['aperture'] / 2)


@pytest.mark.parametrize('bad', [{'quality': []}, {'enabled': 1}, {'f_stop': float('nan')}, {'f_stop': 0}, {'blades': 2}, {'model': 'imaginary'}, {'version': True}, {'disabled_pairs': ['5:6']}, {'surfaces': {'0': {'reflection': 2}}}, {'surfaces': {'5': {}}}, {'surfaces': []}, {'mystery': 2}])
def test_reject_invalid_settings(bad):
    with pytest.raises(ValueError): validate_settings(bad)


def test_schema_roundtrip_no_mutation_and_legacy_unchanged():
    raw = dict(schema_version=1, elements=[], lens_lab=settings())
    before = copy.deepcopy(raw)
    out = validate_preset(raw)
    assert load_preset(json.dumps(out)) == out
    assert raw == before
    assert 'lens_lab' not in validate_preset(dict(schema_version=1, elements=[]))


def test_mesh_conserves_energy_on_identity_mapping():
    y, x = torch.meshgrid(torch.arange(1., 9.), torch.arange(1., 9.), indexing='ij')
    out = torch.zeros(100)
    _reconstruct(out, x.flatten()[None], y.flatten()[None], torch.ones(1, 64), 8, 10, 10)
    assert out.sum() == pytest.approx(49, abs=.001)


def test_mesh_rasterizes_largest_non_power_of_two_bucket():
    # Each triangle's pixel box is 3x3=9, requiring the 16-sample bucket.
    y, x = torch.meshgrid(torch.arange(1., 16., 2), torch.arange(1., 16., 2), indexing='ij')
    out = torch.zeros(400)
    _reconstruct(out, x.flatten()[None], y.flatten()[None], torch.ones(1, 64), 8, 20, 20)
    assert out.sum() == pytest.approx(49, abs=.001)


def test_full_render_finite_and_occlusion_attenuates_not_rescales():
    config = validate_settings({'quality':'draft'})
    a = render_lens(config, [LIGHT], 49, 81, grid_size=24)
    b = render_lens(config, [dict(LIGHT, occlusion=.75)], 49, 81, grid_size=24)
    assert a.shape == (49, 81, 3) and torch.isfinite(a).all() and a.max() > 0
    assert torch.allclose(b, a * .25, atol=1e-6)
    assert render_lens(config, [dict(LIGHT, occlusion=1.)], 49, 81).count_nonzero() == 0


def test_reflection_suppression_and_disabled_paths():
    config = settings(source_glow=0, source_rays=0)
    config['disabled_pairs'] = [f'{a}:{b}' for a, b in ghost_pairs()]
    assert render_lens(config, [LIGHT], 32, 48).count_nonzero() == 0
    config = settings(source_glow=0, source_rays=0, surfaces={str(i): {'reflection': 0} for i in range(13) if i != 5})
    assert render_lens(config, [LIGHT], 32, 48).count_nonzero() == 0


def test_engine_output_buffer_hybrid_and_disabled_mode():
    config = settings()
    p = preset(config, [{'type':'glow'}])
    old = {k:v for k,v in p.items() if k != 'lens_lab'}
    args = ([LIGHT], 32, 48, 'cpu', torch.float32)
    optical = render_stack(p, *args)
    art = render_stack(old, *args)
    p['lens_lab']['include_artistic'] = True
    out = torch.zeros_like(optical)
    assert render_stack(p, *args, out=out) is out
    assert torch.allclose(out, optical + art, atol=1e-6)
    p['lens_lab']['enabled'] = False
    assert torch.equal(render_stack(p, *args), art)


def test_batch_chunk_invariance():
    p = preset()
    lights = [[LIGHT], [dict(LIGHT, u=.3)]]
    both = render_batch(p, lights, 25, 37, 'cpu', torch.float32)
    one = torch.cat([render_batch(p, [l], 25, 37, 'cpu', torch.float32) for l in lights])
    assert torch.equal(both, one)


def test_diagram_same_model_and_pair():
    d = diagram(settings(sensor_shift=1.5), pair=(6,7))
    assert d['pair'] == '6:7'
    assert len(d['paths']) == 7
    assert d['model']['sensor_z'] == pytest.approx(49.189403, abs=1e-5)
    assert all(math.isfinite(c) for ray in d['paths'] for p in ray for c in p)


def test_node_odd_portrait_half_and_inactive_missing_texture():
    from test_nodes import run_node
    p = preset(elements=[{'type':'texture','params':{'file':'absent.png'}}])
    img = torch.zeros(1, 41, 29, 3, dtype=torch.float16)
    result = run_node(img, preset_json=json.dumps(p), colorspace='linear', visibility_mode='off', light_travel=.1, clamp_output=False)
    assert result[0].shape == img.shape and result[1].dtype == img.dtype
    assert torch.isfinite(result[1]).all() and result[1].max() > 0


@pytest.mark.skipif(not torch.cuda.is_available(), reason='CUDA unavailable')
def test_cpu_cuda_agree():
    p = settings()
    a = render_lens(p, [LIGHT], 35, 57, grid_size=24)
    b = render_lens(p, [LIGHT], 35, 57, device='cuda', grid_size=24).cpu()
    assert torch.allclose(a, b, atol=.003, rtol=.03)


def test_preview_validation():
    from conftest import load_package
    load_package()
    from comfyui_flarecore.nodes.lens_lab_api import preview_payload
    assert preview_payload({})[1:3] == (.7, .3)
    for raw in [None, {'u':float('inf')}, {'u':3}, {'refine':1}, {'pair':'5:6'}, {'width':50000}]:
        with pytest.raises(ValueError): preview_payload(raw)


def test_physical_groups_equal_sum_and_travel_does_not_change_source():
    from test_nodes import run_node
    from test_groups import group, render
    image = torch.zeros(2, 25, 37, 3)
    a, b = group('a', .3), group('b', .7)
    a['preset'] = preset(); b['preset'] = preset()
    combined = render(image, [a,b], colorspace='linear', clamp_output=False)[1]
    separate = sum(run_node(image, preset_json=json.dumps(g['preset']), colorspace='linear', clamp_output=False, **g['source'])[1] for g in [a,b])
    assert torch.allclose(combined, separate, atol=1e-6)
    slow = run_node(image, preset_json=json.dumps(a['preset']), colorspace='linear', visibility_mode='off', position_mode='path', light_path='.3,.4;.7,.4', light_travel=0)[1]
    full = run_node(image, preset_json=json.dumps(a['preset']), colorspace='linear', visibility_mode='off', position_mode='path', light_path='.3,.4;.7,.4', light_travel=1)[1]
    # The portable environment can select CUDA; overlapping scatter additions
    # may differ by floating-point summation order, not source position.
    assert torch.allclose(slow, full, atol=1e-6, rtol=1e-5)
