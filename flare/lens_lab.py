# SPDX-License-Identifier: Apache-2.0
"""Experimental spherical optics, independent of ComfyUI and the layer engine.

All lengths are mm. Light travels along +z; a positive radius has its sphere
centre behind its vertex. Paths contain exactly two surface reflections.
Geometry uses constant-index glass. Single-film spectral transport is designed,
not a measured commercial coating. No textures or authored ghost positions.
"""
import copy
import math
from functools import lru_cache

import torch
import torch.nn.functional as F
from .lens_finish import WAVELENGTHS, film_reflectance, spectral_rgb_weights, source_finish
from .lens_raster import pixel_integral

SOURCE = "https://pbr-book.org/3ed-2018/Camera_Models/Realistic_Cameras#LensSystemRepresentation"
# Numeric prescription: PBRT 3rd ed., Table 6.1, wide.22mm.dat.
# radius, distance to next vertex, index AFTER interface, clear diameter.
# The table's minus signs are SVGs: verified against the original HTML.
_ROWS = (
    (35.98738, 1.21638, 1.54, 23.716),
    (11.69718, 9.9957, 1., 17.996),
    (13.08714, 5.12622, 1.772, 12.364),
    (-22.63294, 1.76924, 1.617, 9.812),
    (71.05802, .8184, 1., 9.152),
    (0., 2.27766, 1., 8.756),
    (-9.58584, 2.43254, 1.617, 8.184),
    (-11.28864, .11506, 1., 9.152),
    (-166.7765, 3.09606, 1.713, 10.648),
    (-7.5911, 1.32682, 1.805, 11.44),
    (-16.7662, 3.98068, 1., 12.276),
    (-7.70286, 1.21638, 1.617, 13.42),
    (-11.97328, 0., 1., 17.996),
)
MODEL_ID = "pbrt-wide-22"
DEFAULTS = dict(version=1, enabled=True, model=MODEL_ID, f_stop=4., blades=7,
                rotation=0., sensor_width=36., sensor_shift=0., exposure=6.,
                quality="fine", include_artistic=False, disabled_pairs=[],
                surfaces={'0': {'reflection': .55}, '1': {'reflection': .35},
                          '2': {'reflection': .55}, '4': {'reflection': .08},
                          '6': {'reflection': .10}, '11': {'reflection': .06}},
                coating_profile='mixed', coating_nm=650., coating_strength=1.,
                source_glow=.65, source_rays=1., source_size=.008, source_style='soft')
QUALITY = {"draft": 48, "standard": 96, "fine": 192}
_COATING_OFFSETS = (-200,400,0,-150,300,0,400,-170,0,400,-150,200,-200)


def _number(value, key, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"lens_lab.{key} must be a finite number")
    if not lo <= value <= hi:
        raise ValueError(f"lens_lab.{key} must be between {lo} and {hi}")
    return float(value)


def ghost_pairs():
    indices = [i for i, row in enumerate(_ROWS) if row[0] != 0]
    return [(a, b) for b in indices for a in indices if a < b]


def validate_settings(raw):
    if not isinstance(raw, dict):
        raise ValueError("lens_lab must be an object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown lens_lab settings: {', '.join(sorted(unknown))}")
    out = copy.deepcopy(DEFAULTS)
    out.update(copy.deepcopy(raw))
    if type(out['version']) is not int or out['version'] != 1 or out['model'] != MODEL_ID:
        raise ValueError("Unsupported lens_lab version or model")
    for key in ('enabled', 'include_artistic'):
        if type(out[key]) is not bool:
            raise ValueError(f"lens_lab.{key} must be boolean")
    for key, lo, hi in [('f_stop', 2.8, 22), ('rotation', -180, 180),
                        ('sensor_width', 16, 70), ('sensor_shift', -5, 5), ('exposure', -10, 16),
                        ('coating_nm', 380, 1800), ('coating_strength', 0, 1),
                        ('source_glow', 0, 4), ('source_rays', 0, 8), ('source_size', .001, .05)]:
        out[key] = _number(out[key], key, lo, hi)
    if type(out['blades']) is not int or out['blades'] not in (0, *range(3, 17)):
        raise ValueError("lens_lab.blades must be 0 (circle), or 3 through 16")
    if not isinstance(out['quality'], str) or out['quality'] not in QUALITY:
        raise ValueError("lens_lab.quality must be draft, standard or fine")
    if out['coating_profile'] not in ('mixed', 'uniform'):
        raise ValueError('lens_lab.coating_profile must be mixed or uniform')
    if out['source_style'] not in ('soft', 'rays'):
        raise ValueError('lens_lab.source_style must be soft or rays')
    valid = {f'{a}:{b}' for a, b in ghost_pairs()}
    disabled = out['disabled_pairs']
    if not isinstance(disabled, list) or len(disabled) > len(valid) or any(not isinstance(p, str) or p not in valid for p in disabled):
        raise ValueError("lens_lab.disabled_pairs contains an unknown reflection path")
    out['disabled_pairs'] = sorted(set(disabled))
    surfaces = out['surfaces']
    if not isinstance(surfaces, dict) or len(surfaces) > len(_ROWS):
        raise ValueError("lens_lab.surfaces must be a bounded object")
    for key, overrides in surfaces.items():
        if key not in {str(i) for i in range(len(_ROWS)) if _ROWS[i][0]}:
            raise ValueError("Unknown lens surface")
        if not isinstance(overrides, dict) or set(overrides) - {'reflection', 'coating_nm'}:
            raise ValueError("Surface overrides support reflection and coating_nm")
        if 'reflection' in overrides:
            overrides['reflection'] = _number(overrides['reflection'], 'surface.reflection', 0, 1)
        if 'coating_nm' in overrides:
            overrides['coating_nm'] = _number(overrides['coating_nm'], 'surface.coating_nm', 380, 1800)
    return out


@lru_cache(maxsize=1)
def _base_model():
    # Paraxial ray of height 1 mm and angle 0. Used ONLY to establish the
    # infinity sensor plane and entrance-pupil/f-number mapping, not ghosts.
    z, n, y, theta, stop_height = 0., 1., 1., 0., 0.
    surfaces = []
    for i, (radius, gap, after, diameter) in enumerate(_ROWS):
        surfaces.append(dict(id=i, z=z, radius=radius, aperture=diameter / 2,
                             before=n, after=after, stop=radius == 0))
        if radius:
            theta = n / after * theta - y * (after - n) / (after * radius)
        else:
            stop_height = y
        y += gap * theta
        z += gap
        n = after
    focal = -1. / theta
    sensor = z - y / theta
    return dict(id=MODEL_ID, name='PBRT Wide 22 mm · reference prime',
                provenance='Published educational prescription; not a measured commercial lens.',
                source=SOURCE, focal_length=focal, sensor_z=sensor,
                stop_height=stop_height, surfaces=surfaces)


def model_for(settings):
    model = copy.deepcopy(_base_model())
    model['sensor_z'] += settings['sensor_shift']
    for surface in model['surfaces']:
        surface['reflection'] = settings['surfaces'].get(str(surface['id']), {}).get('reflection', 1.)
        base_coating = settings['coating_nm'] + (_COATING_OFFSETS[surface['id']] if settings['coating_profile']=='mixed' else 0)
        surface['coating_nm'] = settings['surfaces'].get(str(surface['id']), {}).get('coating_nm', max(380,min(1800,base_coating)))
        if surface['stop']:
            surface['aperture'] = min(surface['aperture'],
                abs(model['stop_height']) * model['focal_length'] / (2 * settings['f_stop']))
    model['sensor_width'] = settings['sensor_width']
    return model


def catalog():
    return dict(defaults=copy.deepcopy(DEFAULTS), models=[copy.deepcopy(_base_model())], coating_offsets=list(_COATING_OFFSETS),
                looks=[dict(id='balanced', name='Balanced · varied coatings', settings=dict(coating_profile='mixed',coating_nm=650.,coating_strength=1.,surfaces=copy.deepcopy(DEFAULTS['surfaces']),exposure=6.,source_glow=.65,source_rays=1.,source_size=.008)),
                       dict(id='cool', name='Cool · restrained blue', settings=dict(coating_profile='uniform',coating_nm=650.,coating_strength=1.,surfaces=copy.deepcopy(DEFAULTS['surfaces']),exposure=6.,source_glow=.65,source_rays=1.,source_size=.008)),
                       dict(id='warm', name='Warm · vintage amber', settings=dict(coating_profile='uniform',coating_nm=440.,coating_strength=.85,surfaces=copy.deepcopy(DEFAULTS['surfaces']),exposure=6.,source_glow=.7,source_rays=.6,source_size=.009)),
                       dict(id='bare', name='Uncoated · optical diagnostic', settings=dict(coating_profile='uniform',coating_strength=0.,surfaces={},exposure=6.,source_glow=0.,source_rays=0.,source_size=.008))],
                pairs=[dict(id=f'{a}:{b}', surfaces=[a, b], label=f'S{b + 1} ↔ S{a + 1}') for a, b in ghost_pairs()])


def fresnel(cos_i, eta_i, eta_t):
    """Unpolarized power reflectance; total internal reflection returns 1."""
    sin_t2 = (eta_i / eta_t) ** 2 * (1 - cos_i.square()).clamp(0, 1)
    cos_t = (1 - sin_t2).clamp_min(0).sqrt()
    rs = (eta_i * cos_i - eta_t * cos_t) / (eta_i * cos_i + eta_t * cos_t).clamp_min(1e-12)
    rp = (eta_t * cos_i - eta_i * cos_t) / (eta_t * cos_i + eta_i * cos_t).clamp_min(1e-12)
    reflectance = .5 * (rs.square() + rp.square())
    return torch.where(sin_t2 >= 1, torch.ones_like(reflectance), reflectance), cos_t, sin_t2 < 1


def aperture_mask(x, y, radius, blades, rotation):
    radial = x.square() + y.square() <= radius.square()
    if not blades:
        return radial
    # radius is the circumradius of the blade polygon.
    sector = 2 * math.pi / blades
    angle = torch.remainder(torch.atan2(y, x) - math.radians(rotation) + sector / 2, sector) - sector / 2
    limit = radius * math.cos(math.pi / blades) / angle.cos()
    return radial & (x.square() + y.square() <= limit.square())


def aperture_clearance(x, y, radius, blades=0, rotation=0):
    """Continuous signed aperture field: positive inside, zero at the rim.

    Keeping this field (instead of killing boundary vertices) allows the image
    reconstruction to clip BETWEEN rays. This is essential at practical grids.
    """
    distance = torch.sqrt(x.square() + y.square())
    if blades:
        sector = 2 * math.pi / blades
        angle = torch.remainder(torch.atan2(y, x) - math.radians(rotation) + sector / 2, sector) - sector / 2
        distance = distance * angle.cos() / math.cos(math.pi / blades)
    return 1 - distance / radius.clamp_min(1e-12)


def intersect_surface(origin, direction, z, radius):
    """Nearest positive hit on the vertex hemisphere (not the other sphere cap)."""
    planar = radius == 0
    centre = torch.stack((torch.zeros_like(z), torch.zeros_like(z), z + radius), -1)
    oc = origin - centre
    b = (oc * direction).sum(-1)
    c = oc.square().sum(-1) - radius.square()
    disc = b.square() - c
    root = disc.clamp_min(0).sqrt()
    best = torch.full_like(b, float('inf'))
    for t in (-b - root, -b + root):
        hit_z = origin[..., 2] + t * direction[..., 2]
        cap = (hit_z - z - radius) * radius <= 1e-7
        good = (disc >= 0) & (t > 1e-6) & cap
        best = torch.minimum(best, torch.where(good, t, torch.full_like(t, float('inf'))))
    dz = direction[..., 2]
    plane_t = (z - origin[..., 2]) / torch.where(dz.abs() > 1e-12, dz, torch.ones_like(dz))
    best = torch.where(planar, plane_t, best)
    valid = torch.isfinite(best) & (best > 1e-6) & (~planar | (dz.abs() > 1e-12))
    hit = origin + torch.where(valid, best, torch.zeros_like(best))[..., None] * direction
    normal = F.normalize(hit - centre, dim=-1)
    normal = torch.where(planar[..., None], torch.tensor([0., 0., 1.], device=origin.device, dtype=origin.dtype), normal)
    normal = torch.where(((normal * direction).sum(-1) > 0)[..., None], -normal, normal)
    return hit, normal, valid


def _itinerary(pair, count):
    if pair is None:
        return [(i, False, True) for i in range(count)]
    a, b = pair
    return ([(i, i == b, True) for i in range(b + 1)] +
            [(i, i == a, False) for i in range(b - 1, a - 1, -1)] +
            [(i, False, True) for i in range(a + 1, count)])


def trace(model, settings, u, v, aspect, pupil, pairs, record=False, footprint=False, spectral=False):
    """Vectorized [path, pupil sample] trace; diagram uses these same intersections."""
    device, dtype = pupil.device, pupil.dtype
    count, rays = len(pairs), pupil.shape[-2]
    # Invert the physical sensor image, so a direct source lands at UI u,v.
    sensor_h = settings['sensor_width'] / aspect
    slope = [-(u - .5) * settings['sensor_width'] / model['focal_length'],
             -(v - .5) * sensor_h / model['focal_length'], 1.]
    direction = F.normalize(torch.tensor(slope, device=device, dtype=dtype), dim=0).expand(count, rays, 3).clone()
    origin = torch.zeros_like(direction)
    origin[..., :2] = pupil
    origin[..., 2] = -1.
    energy = torch.ones((count, rays), device=device, dtype=dtype)
    valid = torch.ones_like(energy, dtype=torch.bool)
    clearance = torch.ones_like(energy)
    if spectral:
        wavelengths = torch.tensor(WAVELENGTHS, device=device, dtype=dtype)
        energy = energy[..., None].expand(count, rays, len(WAVELENGTHS)).clone()
    history = [origin.clone()] if record else None
    itineraries = [_itinerary(pair, len(model['surfaces'])) for pair in pairs]
    fields = {key: torch.tensor([s[key] for s in model['surfaces']], device=device, dtype=dtype)
              for key in ('z', 'radius', 'aperture', 'before', 'after', 'reflection', 'coating_nm')}
    for step in range(max(map(len, itineraries))):
        active = torch.tensor([step < len(p) for p in itineraries], device=device)[:, None]
        ops = [p[min(step, len(p) - 1)] for p in itineraries]
        idx = torch.tensor([p[0] for p in ops], device=device)
        reflect = torch.tensor([p[1] for p in ops], device=device)[:, None]
        forward = torch.tensor([p[2] for p in ops], device=device)[:, None]
        values = {k: val[idx, None] for k, val in fields.items()}
        hit, normal, hit_valid = intersect_surface(origin, direction, values['z'], values['radius'])
        inside = hit[..., 0].square() + hit[..., 1].square() <= values['aperture'].square()
        stop = values['radius'] == 0
        inside &= (~stop | aperture_mask(hit[..., 0], hit[..., 1], values['aperture'], settings['blades'], settings['rotation']))
        if footprint:
            rim = aperture_clearance(hit[..., 0], hit[..., 1], values['aperture'])
            blade_rim = aperture_clearance(hit[..., 0], hit[..., 1], values['aperture'], settings['blades'], settings['rotation'])
            clearance = torch.minimum(clearance, torch.where(active, torch.where(stop, blade_rim, rim), clearance))
        ni = torch.where(forward, values['before'], values['after'])
        nt = torch.where(forward, values['after'], values['before'])
        cos_i = -(direction * normal).sum(-1).clamp(-1, 0)
        refl, cos_t, transmitted = fresnel(cos_i, ni, nt)
        if spectral:
            coated = film_reflectance(cos_i, ni, nt, values['coating_nm'], wavelengths, refl, cos_t, transmitted)
            refl = torch.lerp(refl[..., None].expand_as(coated), coated, settings['coating_strength'])
            refl = torch.where(transmitted[..., None], refl * values['reflection'][..., None], refl)
        else:
            refl = torch.where(transmitted, refl * values['reflection'], refl)
        eta = ni / nt
        refracted = eta[..., None] * direction + (eta * cos_i - cos_t)[..., None] * normal
        reflected = direction + 2 * cos_i[..., None] * normal
        outgoing = torch.where(reflect[..., None], reflected, refracted)
        # Reconstruction needs sensor coordinates just outside the clear
        # opening. Continue those geometric rays, carrying aperture clearance
        # separately. Physically impossible sphere/TIR paths still terminate.
        passed = hit_valid & (True if footprint else inside) & (reflect | transmitted)
        valid &= ~active | passed
        weight = torch.where(stop[..., None] if spectral else stop, torch.ones_like(refl),
                             torch.where(reflect[..., None] if spectral else reflect, refl, 1 - refl))
        energy *= torch.where(active[..., None] if spectral else active, weight, torch.ones_like(weight))
        update = active[..., None] & valid[..., None]
        origin = torch.where(update, hit, origin)
        direction = torch.where(update, F.normalize(outgoing, dim=-1), direction)
        if record:
            history.append(torch.where(valid[..., None], origin, torch.full_like(origin, float('nan'))))
    dz = direction[..., 2]
    distance = (model['sensor_z'] - origin[..., 2]) / torch.where(dz.abs() > 1e-12, dz, torch.ones_like(dz))
    valid &= (dz > 1e-8) & (distance > 0)
    sensor = origin + distance[..., None] * direction
    energy = torch.where(valid[..., None] if spectral else valid, energy, torch.zeros_like(energy))
    if record:
        history.append(torch.where(valid[..., None], sensor, torch.full_like(sensor, float('nan'))))
    if footprint:
        return sensor, energy, history, torch.where(valid, clearance, -torch.ones_like(clearance))
    return sensor, energy, history


def pupil_grid(n, radius, device, dtype):
    p = (torch.arange(n, device=device, dtype=dtype) + .5) / n * 2 - 1
    y, x = torch.meshgrid(p, p, indexing='ij')
    return torch.stack((x.flatten(), y.flatten()), -1) * radius


def fitted_pupils(model, settings, u, v, aspect, pairs, n, device, dtype):
    """Spend the render grid on each ghost's contributing entrance footprint.

    A deterministic scouting grid finds a conservatively padded box. Its
    density follows f-number: a fixed wide-open grid can miss the tiny entrance
    footprint of a stopped-down reflection completely, intermittently falling
    back to the whole entrance pupil and losing the ghost between frames.
    No random resampling between frames. Cells just outside the opening are
    retained so their signed aperture field can clip the image smoothly.
    """
    radius = model['surfaces'][0]['aperture']
    scout_n = max(48, math.ceil(12 * settings['f_stop']))
    scout = pupil_grid(scout_n, radius, device, dtype)
    _, energy, _, margin = trace(model, settings, u, v, aspect, scout, pairs, footprint=True)
    near = (margin > -.25) & (energy > 0)
    pad = 2 * radius / scout_n * 2
    lower = torch.where(near[..., None], scout[None], float('inf')).amin(1) - pad
    upper = torch.where(near[..., None], scout[None], -float('inf')).amax(1) + pad
    found = near.any(-1)
    lower = torch.where(found[:, None], lower.clamp(-radius, radius), -radius)
    upper = torch.where(found[:, None], upper.clamp(-radius, radius), radius)
    extent = (upper - lower).clamp_min(1e-6)
    unit = (pupil_grid(n, 1., device, dtype) + 1) * .5
    pupil = lower[:, None, :] + unit[None] * extent[:, None, :]
    return pupil, extent.prod(-1) / (n * n)


def _splat(plane, x, y, energy, height, width):
    ix, iy = x.floor().long(), y.floor().long()
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        xx, yy = ix + dx, iy + dy
        weight = (1 - (x - xx).abs()) * (1 - (y - yy).abs())
        good = (xx >= 0) & (xx < width) & (yy >= 0) & (yy < height)
        index = yy.clamp(0, height - 1) * width + xx.clamp(0, width - 1)
        plane.scatter_add_(0, index[:, None].expand(-1, plane.shape[1]),
                           torch.where(good[:, None], weight[:, None] * energy, 0))


def _positive_triangle_fraction(margin):
    """Area fraction after clipping a triangle by a linear signed field."""
    ordered = margin.sort(dim=-1).values
    lo, mid, hi = ordered.unbind(-1)
    one = hi.square() / ((hi - lo) * (hi - mid)).clamp_min(1e-20)
    two = 1 - lo.square() / ((lo - mid) * (lo - hi)).clamp_min(1e-20)
    return torch.where(lo >= 0, 1., torch.where(hi <= 0, 0., torch.where(mid <= 0, one, two))).clamp(0, 1)


def _reconstruct(plane, x, y, energy, n, height, width, clearance=None, tiny_limit=0.):
    """Rasterize traced pupil triangles with area-conserving irradiance.

    Aperture clipping happens in the interpolated footprint, NOT by discarding
    an entire triangle when one vertex misses the opening. Folded triangles
    contribute independently (caustics).
    Nondegenerate footprints use exact coverage even below a pixel. Only truly
    collapsed points use a flux-preserving splat. Collapsed lines are widened
    by at most .001 pixel for a finite, conservative area integral.
    Temporary sample arrays are bounded, independently of output resolution.
    """
    device, dtype = x.device, x.dtype
    # Share raster coverage across RGB; do not rasterize each wavelength/channel.
    if plane.ndim == 1:
        plane = plane[:, None]
        energy = energy[..., None]
    channels = plane.shape[1]
    cells = torch.arange(n * n, device=device).reshape(n, n)[:-1, :-1].flatten()
    ids = torch.cat((torch.stack((cells, cells + 1, cells + n), -1),
                     torch.stack((cells + 1, cells + n + 1, cells + n), -1)))
    points = torch.stack((x, y), -1)[:, ids].reshape(-1, 3, 2)
    weights = energy[:, ids].reshape(-1, 3, channels)
    margins = torch.ones_like(weights[..., 0]) if clearance is None else clearance[:, ids].reshape(-1, 3)
    good = (weights.abs().sum(-1) > 0).all(-1) & torch.isfinite(points).all(-1).all(-1) & (margins.amax(-1) > 0)
    points, weights, margins = points[good], weights[good], margins[good]
    if not len(points):
        return
    lo, hi = points.amin(1), points.amax(1)
    visible = (hi[:, 0] >= -.5) & (lo[:, 0] <= width - .5) & (hi[:, 1] >= -.5) & (lo[:, 1] <= height - .5)
    points, weights, margins, lo, hi = points[visible], weights[visible], margins[visible], lo[visible], hi[visible]
    if not len(points):
        return
    edge1, edge2 = points[:, 1]-points[:, 0], points[:, 2]-points[:, 0]
    determinant = edge1[:, 0]*edge2[:, 1]-edge1[:, 1]*edge2[:, 0]
    singular = determinant.abs() <= 1e-10
    extent = (hi-lo).amax(-1)
    tiny = (extent < tiny_limit) | (singular & (extent < 1e-4))
    centers = points[tiny].mean(1)
    if len(centers):
        # Integrate the clipped *input* triangle for singular point flux;
        # mean(vertex weights) times area is wrong for a partly clipped ramp.
        reference = torch.tensor([[0.,0.],[1.,0.],[0.,1.]],device=device,dtype=dtype).expand(len(centers),-1,-1)
        half = torch.full((len(centers),1),.5,device=device,dtype=dtype)
        flux = pixel_integral(reference,weights[tiny],margins[tiny],half,half,torch.ones_like(half,dtype=torch.bool))[:,0]
        _splat(plane, centers[:, 0], centers[:, 1], flux, height, width)
    line = singular & ~tiny
    if bool(line.any()):
        # Avoid dropping energy at a singular caustic. Use the longer edge so
        # coincident vertices are safe. The infinitesimal ribbon is bounded
        # in output pixels, not a global blur of ordinary ghost footprints.
        choose1=edge1.square().sum(-1)>=edge2.square().sum(-1)
        edge=torch.where(choose1[:,None],edge1,edge2)
        normal=torch.stack((-edge[:,1],edge[:,0]),-1)/edge.norm(dim=-1,keepdim=True).clamp_min(1e-20)
        points=points.clone()
        points[:,2]+=torch.where((line & choose1)[:,None],normal*.001,0.)
        points[:,1]+=torch.where((line & ~choose1)[:,None],normal*.001,0.)
        lo,hi=points.amin(1),points.amax(1)
    points, weights, margins, lo, hi = points[~tiny], weights[~tiny], margins[~tiny], lo[~tiny], hi[~tiny]
    if not len(points):
        return
    lo = (lo - .5).ceil().clamp_min(0).long()
    hi = (hi + .5).floor().long()
    hi[:, 0].clamp_(max=width - 1); hi[:, 1].clamp_(max=height - 1)
    spans = (hi - lo + 1).clamp_min(0)
    # Bucket by bounding-box area, then use bounded per-triangle pixel offsets.
    sizes = spans[:, 0] * spans[:, 1]
    for power in range(0, int(sizes.max().item()).bit_length() + 1):
        select = (sizes > (0 if power == 0 else 2 ** (power - 1))) & (sizes <= 2 ** power)
        ids_in_bucket = select.nonzero().flatten()
        batch = max(1, 65536 // (2 ** power))
        for start in range(0, len(ids_in_bucket), batch):
            take = ids_in_bucket[start:start + batch]
            p, wgt, lower, span, rim = points[take], weights[take], lo[take], spans[take], margins[take]
            offset = torch.arange(int(sizes[take].max().item()), device=device)[None, :]
            px = lower[:, 0, None] + offset % span[:, 0, None].clamp_min(1)
            py = lower[:, 1, None] + offset // span[:, 0, None].clamp_min(1)
            valid = offset < sizes[take, None]
            a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
            det = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
            safe_det = torch.where(det.abs() > 1e-10, det, torch.ones_like(det))[:, None]
            value = pixel_integral(p, wgt, rim, px, py, valid & (det.abs()[:,None]>1e-10)) / safe_det.abs()[...,None]
            index = py.clamp(0, height - 1) * width + px.clamp(0, width - 1)
            plane.scatter_add_(0, index.flatten()[:, None].expand(-1, channels), value.reshape(-1, channels))


@torch.no_grad()
def render_lens(settings, lights, height, width, device='cpu', dtype=torch.float32, grid_size=None):
    if dtype in (torch.float16, torch.bfloat16):
        return render_lens(settings, lights, height, width, device, torch.float32, grid_size).to(dtype)
    settings = validate_settings(settings)
    out = torch.zeros((height, width, 3), device=device, dtype=dtype)
    if not settings['enabled']:
        return out
    if len(lights) > 16:
        raise ValueError('Lens Lab supports up to 16 lights per frame; reduce detect_max_lights.')
    model = model_for(settings)
    pairs = [p for p in ghost_pairs() if f'{p[0]}:{p[1]}' not in settings['disabled_pairs']]
    n = grid_size or QUALITY[settings['quality']]
    if not 8 <= n <= 384:
        raise ValueError('Lens Lab ray grid must be 8..384 (above 192 is for offline convergence checks)')
    sensor_w, sensor_h = settings['sensor_width'], settings['sensor_width'] * height / width
    # Quadrature area / pixel area: no per-ghost maximum normalization.
    factor = 1 / (sensor_w * sensor_h / (width * height))
    factor *= 2 ** settings['exposure']
    for light in lights:
        u = light.get('u', .5 + light.get('x', 0.) / (2 * width / height))
        v = light.get('v', .5 + light.get('y', 0.) / 2)
        strength = max(0., float(light.get('brightness', 1.))) * (1 - min(1., max(0., float(light.get('occlusion', 0.)))))
        if not all(math.isfinite(float(t)) for t in (u, v, strength)):
            raise ValueError('Lens Lab light coordinates/brightness must be finite')
        if strength <= 0:
            continue
        plane = torch.zeros(height * width, 3, device=device, dtype=dtype)
        rgb_weights = spectral_rgb_weights(device, dtype)
        # Small path batches keep CPU operations below expensive threading
        # thresholds and bound GPU memory independently of final image size.
        path_batch = 24 if torch.device(device).type == 'cuda' else 4
        for start in range(0, len(pairs), path_batch):
            batch_pairs = pairs[start:start + path_batch]
            pupil, sample_area = fitted_pupils(model, settings, u, v, width / height, batch_pairs, n, device, dtype)
            sensor, energy, _, clearance = trace(model, settings, u, v, width / height, pupil, batch_pairs, footprint=True, spectral=True)
            # Preserve signed RGB quadrature until the complete pass is summed.
            energy = energy @ rgb_weights
            x = (.5 - sensor[..., 0] / sensor_w) * width - .5
            y = (.5 - sensor[..., 1] / sensor_h) * height - .5
            _reconstruct(plane, x, y, energy * sample_area[:, None, None], n, height, width, clearance)
        # Exact area coverage supplies antialiasing. A post-splat pixel blur
        # here changed narrow caustic peaks with output resolution, even when
        # the native and high-resolution integrated footprints matched.
        plane = plane.view(height, width, 3).clamp_min(0)
        color = torch.as_tensor(light.get('color', [1., 1., 1.]), device=device, dtype=dtype)
        finish = source_finish(settings, u, v, height, width, device, dtype)
        out += (plane * factor + finish) * color * strength
    return out


def diagram(settings, u=.7, v=.3, aspect=16 / 9, pair=None):
    settings = validate_settings(settings)
    model = model_for(settings)
    if pair is not None and pair not in ghost_pairs():
        raise ValueError('Unknown diagram reflection path')
    # Meridional section in the source's radial plane; y is the radial axis.
    sx = (u - .5) * settings['sensor_width']
    sy = (v - .5) * settings['sensor_width'] / aspect
    radial_u = .5 + math.hypot(sx, sy) / settings['sensor_width']
    pupil = torch.zeros(7, 2, dtype=torch.float64)
    pupil[:, 0] = torch.linspace(-.75, .75, 7) * model['surfaces'][0]['aperture']
    _, _, history = trace(model, settings, radial_u, .5, aspect, pupil, [pair], record=True)
    paths = []
    for ray in range(len(pupil)):
        path = []
        for state in history:
            xyz = state[0, ray].tolist()
            if not all(math.isfinite(t) for t in xyz):
                break
            path.append([xyz[2], xyz[0]])
        paths.append(path)
    return dict(model=model, paths=paths, section='Meridional source plane · mm',
                pair=None if pair is None else f'{pair[0]}:{pair[1]}')
