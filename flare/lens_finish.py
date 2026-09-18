# SPDX-License-Identifier: Apache-2.0
"""Spectral coating weights and explicitly artistic source finishing.

Coating transport is a lossless single dielectric film, not measured multilayer
glass. Ray geometry remains constant-index. The source finish is a separately
controllable photographic glow/ray approximation, not a diffraction claim.
"""
import math
import torch

WAVELENGTHS = tuple(range(420, 701, 35))


def spectral_rgb_weights(device, dtype):
    """Nine-band quadrature, analytic CIE 1931 fit (Wyman et al., JCGT 2013).

    Equal-energy illumination is white-balanced to neutral working RGB. This
    is an economical color approximation, not a camera spectral calibration.
    """
    w = torch.tensor(WAVELENGTHS, device=device, dtype=dtype)
    def g(mu, left, right):
        t = (w - mu) * torch.where(w < mu, left, right)
        return torch.exp(-.5 * t.square())
    x = 1.056*g(599.8,.0264,.0323)+.362*g(442.,.0624,.0374)-.065*g(501.1,.049,.0382)
    y = .821*g(568.8,.0213,.0247)+.286*g(530.9,.0613,.0322)
    z = 1.217*g(437.,.0845,.0278)+.681*g(459.,.0385,.0725)
    xyz = torch.stack((x,y,z),-1)
    transform = torch.tensor([[3.2406,-.9689,.0557],[-1.5372,1.8758,-.2040],[-.4986,.0415,1.057]],device=device,dtype=dtype)
    rgb = xyz @ transform
    return rgb / rgb.sum(0)


def film_reflectance(cos_i, ni, nt, design_nm, wavelengths, bare, cos_t, transmitted):
    """s/p-averaged one-layer interference, MgF2-like index 1.38.

    Reciprocal propagation through the film; no coating on cemented interfaces.
    TIR stays TIR. design_nm is the quarter-wave design wavelength, not thickness.
    """
    nf = 1.38
    cos_f = (1 - (ni/nf).square() * (1-cos_i.square()).clamp(0,1)).clamp_min(0).sqrt()
    phase = math.pi * design_nm[...,None] * cos_f[...,None] / wavelengths
    phase_cos = phase.cos()
    result = torch.zeros_like(phase)
    s01=(ni*cos_i-nf*cos_f)/(ni*cos_i+nf*cos_f).clamp_min(1e-12)
    s12=(nf*cos_f-nt*cos_t)/(nf*cos_f+nt*cos_t).clamp_min(1e-12)
    p01=(nf*cos_i-ni*cos_f)/(nf*cos_i+ni*cos_f).clamp_min(1e-12)
    p12=(nt*cos_f-nf*cos_t)/(nt*cos_f+nf*cos_t).clamp_min(1e-12)
    for a,b in ((s01,s12),(p01,p12)):
        product=(a*b)[...,None]
        result += .5*((a.square()+b.square())[...,None]+2*product*phase_cos)/(1+product.square()+2*product*phase_cos).clamp_min(1e-12)
    air_interface = ((ni <= 1.00001) | (nt <= 1.00001)) & (ni != nt)
    result = torch.where(air_interface[...,None], result, bare[...,None])
    return torch.where(transmitted[...,None], result.clamp(0,1), 1.)


def source_finish(settings, u, v, height, width, device, dtype):
    """Declared artistic source core, scatter halo, and blade-oriented rays.

    Fixed low-order angular structure is frame-stable; no animated noise. This
    never substitutes for or normalizes the separate traced ghost pass.
    """
    gain = settings.get('source_glow', 0.)
    rays_gain = settings.get('source_rays', 0.)
    if gain <= 0 and rays_gain <= 0:
        return torch.zeros(height,width,3,device=device,dtype=dtype)
    yy=(torch.arange(height,device=device,dtype=dtype)+.5)/height-v
    xx=((torch.arange(width,device=device,dtype=dtype)+.5)/width-u)*width/height
    y,x=torch.meshgrid(yy,xx,indexing='ij')
    r=torch.sqrt(x.square()+y.square()+1e-12)
    size=settings.get('source_size',.008)
    soft=settings.get('source_style','rays') == 'soft'
    # Analytic photographic approximation, with a finite minimum core width.
    core_size=max(size, .65/height)
    if soft:
        # A continuous middle envelope connects the core to the lower-contrast
        # rays. This is the documented artistic finish, not traced diffraction.
        core=2.2*torch.exp(-.5*(r/(core_size*.8)).square())
        inner=1.3/(1+(r/(core_size*4.4)).square()).pow(1.8)
        outer=.008*torch.exp(-r/(core_size*22))
        angle=torch.atan2(y,x)-math.radians(settings['rotation'])
        blades=settings['blades']
        arms=blades if blades and blades%2==0 else max(6,blades*2)
        broad=(.5+.5*torch.cos(angle*arms+.16*torch.sin(angle*3))).pow(3)
        medium=(.5+.5*torch.cos(angle*(arms*2+2)+.65*torch.sin(angle*5))).pow(14)
        fine=(.5+.5*torch.cos(angle*(arms*4+2)+.7*torch.sin(angle*3)+.3*torch.sin(angle*7))).pow(32)
        modulation=.72+.17*torch.cos(angle*5+1.7)+.11*torch.sin(angle*9)
        reach=core_size*(18+5*torch.sin(angle*3+.7)+2*torch.cos(angle*7))
        rays=(.28*broad+.065*medium+.018*fine)*modulation*torch.exp(-r/reach)/(1+(r/(core_size*4)).square())
        tint=torch.tensor([.48,.72,1.],device=device,dtype=dtype)
        blend=(1-torch.exp(-r/(core_size*2)))[...,None]
        return (gain*core)[...,None]+(gain*(inner+outer)+rays_gain*rays)[...,None]*(1-blend+blend*tint)
    core=5*torch.exp(-.5*(r/core_size).square())
    inner=(.98 if soft else .48)/(1+(r/(core_size*(2.9 if soft else 3.2))).square()).pow(1.4)
    outer=(.023 if soft else .042)*torch.exp(-r/(core_size*17))
    glow=core+inner+outer
    angle=torch.atan2(y,x)-math.radians(settings['rotation'])
    blades=settings['blades']
    arms=blades if blades and blades%2==0 else max(6,blades*2)
    # Wide soft fans underlie finer, irregular rays. Harmonic modulation is
    # fixed in lens space, not resampled per frame or tied to screen pixels.
    broad=(.5+.5*torch.cos(angle*arms+.1*torch.sin(angle*3))).pow(2.2 if soft else 4)
    medium=(.5+.5*torch.cos(angle*arms*2+.32*torch.sin(angle*5))).pow(16)
    fine=(.5+.5*torch.cos(angle*(arms*4+2)+.7*torch.sin(angle*3)+.3*torch.sin(angle*7))).pow(32)
    modulation=.72+.17*torch.cos(angle*5+1.7)+.11*torch.sin(angle*9)
    reach=core_size*(12+4*torch.sin(angle*3+.7)+2*torch.cos(angle*7))
    ray_envelope=torch.exp(-r/reach)/(1+(r/(core_size*3.5)).square())
    rays=((.34*broad+.095*medium+.024*fine) if soft else (1.1*broad+.46*medium+.22*fine))*modulation*ray_envelope
    neutral=(gain*glow+rays_gain*rays)[...,None]
    # Slightly cooler scatter wings; saturated light-source color is multiplied
    # by the caller, so this does not turn a red practical into a blue source.
    tint=torch.tensor([.78,.89,1.],device=device,dtype=dtype)
    blend=(1-torch.exp(-r/(core_size*3)))[...,None]
    return neutral*(1-blend+blend*tint)*(1.1 if soft else 1.)
