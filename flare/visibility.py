# SPDX-License-Identifier: Apache-2.0
"""Source-aperture photometry, independent of the position tracker.

Measures transmitted source energy, not the brightest surviving pixel. The
reference is the 95th percentile aperture flux along each light's trajectory.
This is a relative visibility estimate: exposure changes also affect it, and
a source hidden for the entire clip cannot be reconstructed from the image.
"""
import math

import torch
import torch.nn.functional as F

from .colorspace import luminance


def aperture_flux(image, u, v, radius, background=False):
    """Mean linear radiance in a circular aperture; None outside the image.

    The grid covers pixels densely enough for narrow branches, with a bounded
    working set. Out-of-frame samples are unknown, never copied edge pixels.
    """
    h, w = image.shape[:2]
    if not (0 <= u < 1 and 0 <= v < 1):
        return None
    r = max(float(radius) * h, 1.0)
    n = min(129, max(9, 2 * math.ceil(r) + 1))
    extent = r*3 if background else r
    offsets = torch.linspace(-extent, extent, n, device=image.device, dtype=torch.float32)
    dy, dx = torch.meshgrid(offsets, offsets, indexing='ij')
    x, y = u * w - .5 + dx, v * h - .5 + dy
    distance = dx.square() + dy.square()
    aperture = (distance >= (2*r)**2) & (distance <= (3*r)**2) if background else distance <= r*r
    valid = aperture & (x >= 0) & (x <= w-1) & (y >= 0) & (y <= h-1)
    grid = torch.stack(((x+.5)/w*2-1, (y+.5)/h*2-1), -1)
    lum = image if image.ndim == 2 else luminance(image[..., :3].float()).nan_to_num().clamp_min(0)
    sampled = F.grid_sample(lum[None,None], grid[None], align_corners=False)[0,0]
    return float(sampled[valid].mean()) if bool(valid.any()) else None


def apply_source_visibility(linear_chunk, lights_per_frame, chunk, radius,
                            mode='hybrid', smoothing=0.0):
    """Attach and combine visibility without altering source positions.

    Hybrid takes the stronger obstruction estimate, avoiding double counting
    one branch in both depth and photometry. Dark reference apertures are
    unmeasurable and leave the depth/user estimate untouched.
    """
    if mode not in ('hybrid', 'image'):
        return
    groups = {}
    for start in range(0, len(lights_per_frame), chunk):
        rgb = linear_chunk(start, min(start+chunk, len(lights_per_frame)))
        for offset, frame in enumerate(lights_per_frame[start:start+chunk]):
            lum = luminance(rgb[offset,...,:3].float()).nan_to_num().clamp_min(0)
            for slot, light in enumerate(frame):
                u, v = light.get('source_u', light['u']), light.get('source_v', light['v'])
                flux = aperture_flux(lum, u, v, radius)
                background = aperture_flux(lum, u, v, radius, background=True)
                groups.setdefault(light.get('tid', slot), []).append((start+offset, light, flux, background))
    for entries in groups.values():
        known = sorted(f for _, _, f, _ in entries if f is not None)
        if not known:
            continue
        ref = known[min(len(known)-1, int(.95*(len(known)-1)))]
        if ref < 1e-4:
            continue
        # Estimate the surrounding halo/sky pedestal from clear observations.
        # Freeze it for this trajectory: sampling a dark tree as a new sky
        # background would restore the very light energy we need to remove.
        surrounds = sorted(bg for _,_,f,bg in entries if f is not None and f >= ref*.95 and bg is not None)
        pedestal = surrounds[len(surrounds)//2] if surrounds else 0.
        pedestal = min(pedestal, ref*.9)
        values = [min(max((f-pedestal)/(ref-pedestal), 0.), 1.) if f is not None else 1.
                  for _, _, f, _ in entries]
        # A bounded, symmetric three-frame filter removes chatter without
        # smearing a trunk crossing over seconds. Exact darkness stays dark.
        a = min(max(smoothing, 0.), 1.) * .25
        for k, (frame, light, flux, _) in enumerate(entries):
            value = values[k]
            if flux is not None and value > .005:
                prev = values[k-1] if k and entries[k-1][0] == frame-1 else value
                nxt = values[k+1] if k+1 < len(entries) and entries[k+1][0] == frame+1 else value
                value = (1-2*a)*value + a*(prev+nxt)
            light['source_visibility'] = value
            light['occlusion'] = max(light.get('occlusion', 0.), 1-value)
