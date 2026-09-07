# SPDX-License-Identifier: Apache-2.0
"""Independent flare instances, accumulated in linear light before compositing."""
import json
import math
import torch
from ..flare.schema import load_preset
from ..flare.colorspace import srgb_to_linear, linear_to_srgb, luminance
from ..flare.engine import composite

SOURCE_FIELDS = {
    'position_mode','light_x','light_y','flare_x','flare_y','detect_threshold',
    'detect_max_lights','occlusion_radius','light_depth','invert_depth','seed',
    'occlusion_smooth','scene_color','track_smoothing','track_max_jump',
    'depth_normalize','depth_blur','depth_temporal_smooth','light_path',
    'mask_falloff','light_travel','track_points','track_feature','track_search',
    'track_hold','track_fade','search_radius','scene_lock','anchor_path','visibility_mode',
}


def validate_groups(raw, node_type):
    if type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ValueError('Flare scenes require schema_version 1')
    groups=raw.get('groups')
    if not isinstance(groups,list) or not 1<=len(groups)<=16:
        raise ValueError('A flare scene needs between 1 and 16 groups')
    known=set(); specs=node_type.INPUT_TYPES()['required']
    for group in groups:
        if not isinstance(group,dict): raise ValueError('Flare group must be an object')
        ident=group.get('id')
        if not isinstance(ident,str) or not ident or ident in known:
            raise ValueError('Flare group IDs must be nonempty and unique')
        known.add(ident)
        if not isinstance(group.get('enabled',True),bool):raise ValueError('Group enabled must be boolean')
        preset=group.get('preset')
        if not isinstance(preset,dict) or 'groups' in preset:
            raise ValueError('Each group needs a single preset; nested groups are not supported')
        load_preset(json.dumps(preset))
        source=group.get('source',{})
        if not isinstance(source,dict):raise ValueError('Group source must be an object')
        for key,value in source.items():
            if key=='use_lights_input':
                if not isinstance(value,bool):raise ValueError('use_lights_input must be boolean')
                continue
            if key not in SOURCE_FIELDS:raise ValueError(f'Unsupported group source setting: {key}')
            spec=specs[key]; kind=spec[0]; options=spec[1] if len(spec)>1 else {}
            if isinstance(kind,list):
                if value not in kind:raise ValueError(f'Invalid group {key}')
            elif kind in ('INT','FLOAT'):
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                    raise ValueError(f'Group {key} must be finite')
                if kind=='INT' and int(value)!=value:raise ValueError(f'Group {key} must be an integer')
                if value<options.get('min',-math.inf) or value>options.get('max',math.inf):
                    raise ValueError(f'Group {key} is outside its supported range')
            elif kind=='BOOLEAN' and not isinstance(value,bool):raise ValueError(f'Group {key} must be boolean')
            elif kind=='STRING' and not isinstance(value,str):raise ValueError(f'Group {key} must be text')
    return groups


def render_groups(renderer, raw, args):
    groups=validate_groups(raw,type(renderer))
    image=args['image'];batch,h,w=image.shape[:3]
    dtype=torch.float64 if image.dtype==torch.float64 else torch.float32
    total=torch.zeros((batch,h,w,3),dtype=dtype,device=image.device)
    results={}
    for group in groups:
        if not group.get('enabled',True):
            results[group['id']]={'track':[], 'source':'disabled','status':'Group disabled.'}
            continue
        options=dict(args); options['preset_json']=json.dumps(group['preset'])
        source=group.get('source',{})
        options.update({key:value for key,value in source.items() if key in SOURCE_FIELDS})
        if not source.get('use_lights_input',False):options['lights']=None
        options['_group_pass']=True
        flare,positions,mode=renderer.render(**options)
        total.add_(flare);del flare
        results[group['id']]={
            'track':[[float(frame[0]['u']),float(frame[0]['v']),frame[0].get('au'),frame[0].get('av')]
                     if frame else None for frame in positions],
            'source':mode,'status':renderer._source_status(positions, options['visibility_mode'])}
    # Every group samples the ORIGINAL plate and depth. No group detects or
    # occludes against the flares of groups rendered before it.
    out=torch.empty_like(total);passes=torch.empty_like(total)
    alpha=torch.empty((batch,h,w),dtype=dtype,device=image.device)
    chunk=max(1,int(args.get('chunk_frames',0)) or 8)
    for start in range(0,batch,chunk):
        stop=min(start+chunk,batch)
        plate=image[start:stop,...,:3].to(dtype)
        linear=plate if args['colorspace']=='linear' else srgb_to_linear(plate)
        merged=composite(linear,total[start:stop],args['blend_mode'])
        passed=total[start:stop]
        if args['colorspace']!='linear':merged=linear_to_srgb(merged);passed=linear_to_srgb(passed)
        if args['clamp_output']:merged=merged.clamp(0,1);passed=passed.clamp(0,1)
        out[start:stop]=merged;passes[start:stop]=passed;alpha[start:stop]=luminance(passed).clamp(0,1)
    output_dtype=image.dtype if image.dtype.is_floating_point else torch.float32
    result=(out.to(output_dtype),passes.to(output_dtype),alpha.to(output_dtype))
    from .render import _folder_paths, _save_preview
    if _folder_paths is None:return result
    active=results.get(raw.get('active_group'),next(iter(results.values())))
    return {'result':result,'ui':{'fc_preview':_save_preview(result[0][0])['images'],
            'fc_groups':[results],'fc_track':[active['track']],
            'fc_light_src':[active['source']],'fc_source_status':[active['status']]}}
