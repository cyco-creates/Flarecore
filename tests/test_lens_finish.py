# SPDX-License-Identifier: Apache-2.0
import pytest
import torch
from flare.lens_lab import fresnel, validate_settings, render_lens, ghost_pairs, _positive_triangle_fraction, _reconstruct
from flare.lens_finish import WAVELENGTHS, film_reflectance, spectral_rgb_weights, source_finish
from flare.lens_raster import pixel_integral


def test_spectral_quadrature_white_balance():
    rgb=spectral_rgb_weights('cpu',torch.float64)
    assert torch.allclose(rgb.sum(0),torch.ones(3,dtype=torch.float64))


def test_single_film_normal_incidence_reciprocity_and_quarterwave():
    c=torch.ones(1,dtype=torch.float64)
    for ni,nt in [(1.,1.6),(1.6,1.)]:
        r,ct,ok=fresnel(c,c*ni,c*nt)
        result=film_reflectance(c,c*ni,c*nt,c*550,torch.tensor([550.,275.]),r,ct,ok)[0]
        expected=((ni*nt-1.38**2)/(ni*nt+1.38**2))**2
        assert result[0].item()==pytest.approx(expected,abs=1e-12)
        assert result[1].item()==pytest.approx(r.item(),abs=1e-12)


def test_cemented_interface_and_TIR_do_not_gain_false_film_color():
    c=torch.tensor([1.,.2],dtype=torch.float64)
    ni=torch.tensor([1.6,1.6]);nt=torch.tensor([1.7,1.])
    r,ct,ok=fresnel(c,ni,nt)
    a=film_reflectance(c,ni,nt,torch.full_like(c,650),torch.tensor(WAVELENGTHS),r,ct,ok)
    assert torch.allclose(a[0],r[0].expand_as(a[0]))
    assert (a[1]==1).all()


def test_exact_pixel_integral_clips_area_and_linear_flux():
    points=torch.tensor([[[-3.,-3.],[3.,-3.],[0.,4.]]],dtype=torch.float64)
    flux=(points[...,0]*2+3)[...,None]
    x=torch.zeros(1,1);valid=torch.ones(1,1,dtype=torch.bool)
    full=pixel_integral(points,flux,torch.ones(1,3),x,x,valid)
    half=pixel_integral(points,flux,-points[...,0],x,x,valid)
    assert full.item()==pytest.approx(3,abs=1e-12)
    assert half.item()==pytest.approx(1.25,abs=1e-12)


def test_thin_sliver_is_not_lost_between_coverage_samples():
    p=torch.tensor([[[-.4,-.4],[.4,-.4],[.4,-.399]]],dtype=torch.float64)
    result=pixel_integral(p,torch.ones(1,3,1),torch.ones(1,3),torch.zeros(1,1),torch.zeros(1,1),torch.ones(1,1,dtype=torch.bool))
    assert result.item()==pytest.approx(.0004,abs=1e-12)


def test_triangle_aperture_area_fractions():
    m=torch.tensor([[1,1,1],[-1,-1,-1],[-1,-1,1],[-1,1,1]],dtype=torch.float32)
    assert _positive_triangle_fraction(m).tolist()==pytest.approx([1,0,.25,.75])


@pytest.mark.parametrize('dtype',[torch.float32,torch.float64])
@pytest.mark.parametrize('shape',['point','line','coincident-edge'])
def test_collapsed_footprints_preserve_flux(dtype,shape):
    x=torch.full((1,4),4.,dtype=dtype)
    y=torch.full_like(x,4.2)
    if shape=='line':x=torch.tensor([[2.,6.,2.,6.]],dtype=dtype)
    if shape=='coincident-edge':x=torch.tensor([[2.,2.,6.,6.]],dtype=dtype)
    out=torch.zeros(81,dtype=dtype)
    _reconstruct(out,x,y,torch.ones_like(x),2,9,9)
    assert torch.isfinite(out).all()
    assert out.sum().item()==pytest.approx(1,abs=.002)
    if shape!='point':assert (out.reshape(9,9).sum(0)>0).sum()>=4


def test_collapsed_point_integrates_clipped_linear_flux():
    x=torch.full((1,4),4.,dtype=torch.float64)
    out=torch.zeros(81,dtype=torch.float64)
    _reconstruct(out,x,x,torch.tensor([[1.,3.,1.,3.]],dtype=x.dtype),2,9,9,
                 torch.tensor([[-1.,1.,-1.,1.]],dtype=x.dtype))
    assert out.sum().item()==pytest.approx(1.25,abs=1e-10)


def test_subpixel_coverage_matches_linear_high_resolution_reduction():
    x=torch.tensor([[2.1,6.3,2.2,6.4]],dtype=torch.float64)
    y=torch.tensor([[3.2,3.21,3.22,3.24]],dtype=x.dtype)
    weight=torch.tensor([[1.,2.,3.,4.]],dtype=x.dtype)
    margin=torch.tensor([[-.3,1.,.2,1.]],dtype=x.dtype)
    native=torch.zeros(100,dtype=x.dtype);high=torch.zeros(400,dtype=x.dtype)
    _reconstruct(native,x,y,weight,2,10,10,margin)
    _reconstruct(high,(x+.5)*2-.5,(y+.5)*2-.5,weight,2,20,20,margin)
    # Reconstruction carries pixel-integrated flux. The renderer's sensor
    # pixel-area factor is four times larger at double resolution.
    reduced=high.reshape(10,2,10,2).sum((1,3))
    assert torch.allclose(native.reshape(10,10),reduced,atol=1e-9,rtol=1e-9)


def test_stopped_down_small_ghost_survives_adjacent_source_positions():
    # Historical dropout: fixed48 scout missed path0:2 at f/16 on frames2–5
    # and10–13. It then spent the final grid on the whole pupil, not the ghost.
    cfg=validate_settings(dict(f_stop=16,exposure=9,source_glow=0,source_rays=0,
        disabled_pairs=[f'{a}:{b}' for a,b in ghost_pairs() if (a,b)!=(0,2)]))
    sums=[]
    for frame in [1,3,7,11,14]:
        t=frame/119
        rendered=render_lens(cfg,[dict(u=-.1+1.2*t,v=.1+.8*t)],144,256,grid_size=48)
        sums.append(rendered[...,2].sum().item())
    assert min(sums)>.8*max(sums),sums


def test_finish_is_independent_controllable_and_visibility_linear():
    settings=validate_settings({'disabled_pairs':[f'{a}:{b}' for a,b in ghost_pairs()]})
    a=render_lens(settings,[dict(u=.7,v=.3)],37,59)
    b=source_finish(settings,.7,.3,37,59,'cpu',torch.float32)
    assert torch.equal(a,b) and a.max()>0
    zero=render_lens(dict(settings,source_glow=0,source_rays=0),[dict(u=.7,v=.3)],37,59)
    assert not zero.any()


@pytest.mark.parametrize('bad',[dict(coating_nm=0),dict(coating_strength=2),dict(source_size=0),dict(source_rays=float('nan')),dict(surfaces={'1':{'coating_nm':2000}})])
def test_finish_settings_are_bounded(bad):
    with pytest.raises(ValueError):validate_settings(bad)
