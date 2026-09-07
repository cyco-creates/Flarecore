"""Stateless source-driven optical response curves; no frame history or I/O.

Each channel has its own driver and knots. Smoothstep segments are bounded
and have zero slope at knots, avoiding spline overshoot and unstable video.
The contract is also served to the editor so range tables have one owner.
"""
import math

# target: label, min, max, neutral, application, optional element types
MOTION_TARGETS = {
    'opacity': dict(label='Opacity / energy', min=0., max=8., neutral=1., mode='multiply'),
    'scale': dict(label='Size', min=.05, max=8., neutral=1., mode='multiply'),
    'stretch_x': dict(label='Width', min=.05, max=8., neutral=1., mode='multiply'),
    'stretch_y': dict(label='Height', min=.05, max=8., neutral=1., mode='multiply'),
    'rotation': dict(label='Rotation (degrees)', min=-720., max=720., neutral=0., mode='add'),
    'offset': dict(label='Axis position', min=-4., max=4., neutral=0., mode='add'),
    'shift_x': dict(label='Horizontal shift', min=-4., max=4., neutral=0., mode='add'),
    'shift_y': dict(label='Vertical shift', min=-4., max=4., neutral=0., mode='add'),
    'blur': dict(label='Softness', min=0., max=1., neutral=0., mode='replace'),
    'dispersion': dict(label='Color separation', min=0., max=8., neutral=0., mode='replace'),
    'shade': dict(label='Lit-edge shading', min=-1., max=1., neutral=0., mode='replace'),
    'crescent': dict(label='Barrel clipping', min=0., max=.98, neutral=0., mode='param', types=['iris','ring','hoop','spectral']),
    'roundness': dict(label='Aperture roundness', min=0., max=1., neutral=0., mode='param', types=['iris','spectral']),
    'curve': dict(label='Streak bow', min=-1., max=1., neutral=0., mode='param', types=['streak']),
    'color_r': dict(label='Red transmission', min=0., max=4., neutral=1., mode='multiply'),
    'color_g': dict(label='Green transmission', min=0., max=4., neutral=1., mode='multiply'),
    'color_b': dict(label='Blue transmission', min=0., max=4., neutral=1., mode='multiply'),
}
MOTION_DRIVERS = {
    'radius': dict(label='Source distance from optical center', min=0., max=3., hint='Half-frame-height units; independent of the editable flare anchor.'),
    'x': dict(label='Source horizontal position', min=-1.5, max=1.5, hint='-1 left edge, 0 center, +1 right edge.'),
    'y': dict(label='Source vertical position', min=-1.5, max=1.5, hint='-1 top edge, 0 center, +1 bottom edge.'),
    'edge': dict(label='Source distance inside frame edge', min=-.5, max=1., hint='0 at nearest edge, positive inside, negative outside; half-height units.'),
    'brightness': dict(label='Source brightness', min=0., max=2., hint='Input brightness before occlusion, edge fade and flicker.'),
    'occlusion': dict(label='Source occlusion', min=0., max=1., hint='0 visible; 1 completely occluded.'),
}


def sample_curve(points, value, interpolation='smooth'):
    if value <= points[0][0]:
        return points[0][1]
    for (a, va), (b, vb) in zip(points, points[1:]):
        if value <= b:
            t = (value-a)/(b-a)
            if interpolation == 'smooth':
                t = t*t*(3.-2.*t)
            return va + (vb-va)*t
    return points[-1][1]


def driver_value(driver, light, frame_aspect):
    x, y = light.get('lx', light['x']), light.get('ly', light['y'])
    if driver == 'x':
        return x / max(frame_aspect, 1e-6)
    if driver == 'y':
        return y
    if driver == 'radius':
        return math.hypot(x, y)
    if driver == 'edge':
        return min(frame_aspect-abs(x), 1.-abs(y))
    return light.get('source_'+driver, light.get(driver, 0. if driver == 'occlusion' else 1.))


def apply_motion(element, light, frame_aspect):
    """Return a temporary element plus post-trigger opacity multiplier.

    Never modify the preset, including cached texture tensors. No random draws
    or time accumulation, so visiting the same light position is reproducible.
    """
    motion = element.get('motion')
    if not motion or not motion.get('enabled', True) or not motion['channels']:
        return element, 1.
    e = dict(element)
    e['params'] = dict(element['params'])
    e['stretch'], e['shift'], e['color'] = list(e['stretch']), list(e['shift']), list(e['color'])
    opacity = 1.
    for channel in motion['channels']:
        target = channel['target']
        value = sample_curve(channel['points'], driver_value(channel['driver'], light, frame_aspect), channel['interpolation'])
        if target == 'opacity':
            opacity = value
        elif target in ('stretch_x','stretch_y'):
            e['stretch'][target.endswith('y')] *= value
        elif target in ('shift_x','shift_y'):
            e['shift'][target.endswith('y')] += value
        elif target.startswith('color_'):
            e['color']['rgb'.index(target[-1])] *= value
        elif MOTION_TARGETS[target]['mode'] == 'param':
            e['params'][target] = value
        elif MOTION_TARGETS[target]['mode'] == 'multiply':
            e[target] *= value
        elif MOTION_TARGETS[target]['mode'] == 'add':
            e[target] += value
        else:
            e[target] = value
    return e, opacity
