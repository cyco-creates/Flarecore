# SPDX-License-Identifier: Apache-2.0
"""Connected luminous regions and occlusion-aware source trajectories.

Uses numpy and torch only. Region moments avoid arbitrary pixels on clipped
plateaus. A missing source is a valid state, never an instruction to jump to
the nearest surviving highlight. The full-clip solve retains that identity.
"""
import math
import numpy as np
import torch.nn.functional as F
from .colorspace import luminance
from .track import smooth_series


def detect_sources(image, threshold=.6, max_lights=12, max_size=384):
    b, h, w, _ = image.shape
    lum = luminance(image[..., :3].float()).nan_to_num().clamp_min(0)
    scale = min(1., max_size/max(h,w))
    if scale < 1:
        lum = F.interpolate(lum[:,None], size=(max(1,round(h*scale)),max(1,round(w*scale))), mode='area')[:,0]
    results = []
    for frame in lum.cpu().numpy():
        hh, ww = frame.shape
        mask = frame >= max(threshold, 1e-5)
        # Run-length connected components (8-connected), with union-find.
        parent, runs, previous = [], [], []
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for y, row in enumerate(mask):
            edges = np.flatnonzero(np.diff(np.pad(row.astype(np.int8),(1,1))))
            current = []; prior_start = 0
            for x0, x1 in zip(edges[::2], edges[1::2]):
                idx = len(parent); parent.append(idx)
                runs.append((y,int(x0),int(x1))); current.append(idx)
                while prior_start < len(previous) and runs[previous[prior_start]][2] < x0:
                    prior_start += 1
                for old in previous[prior_start:]:
                    _, a, z = runs[old]
                    if a > x1: break
                    if z >= x0:
                        parent[find(idx)] = find(old)
            previous = current
        stats = {}
        for idx, (y,x0,x1) in enumerate(runs):
            root = find(idx)
            weight = frame[y,x0:x1]
            mass = float(weight.sum())
            st = stats.setdefault(root,[0.,0.,0.,0.,0])
            st[0] += mass; st[1] += float(np.dot(np.arange(x0,x1)+.5,weight))
            st[2] += (y+.5)*mass; st[3] = max(st[3],float(weight.max())); st[4] += x1-x0
        cores = {}
        for idx, (y,x0,x1) in enumerate(runs):
            root = find(idx)
            values = frame[y,x0:x1]
            weight = np.where(values >= .95*stats[root][3], values, 0.)
            mass = float(weight.sum())
            st = cores.setdefault(root,[0.,0.,0.,stats[root][3],0])
            st[0] += mass; st[1] += float(np.dot(np.arange(x0,x1)+.5,weight))
            st[2] += (y+.5)*mass; st[4] += int(np.count_nonzero(weight))
        candidates = []
        for mass,x,y,peak,area in cores.values():
            if mass <= 0: continue
            candidates.append(dict(u=x/mass/ww,v=y/mass/hh,brightness=peak,
                                   energy=mass/(hh*hh),radius=math.sqrt(area/math.pi)/hh))
        candidates.sort(key=lambda d:(-d['energy'],d['v'],d['u']))
        results.append(candidates[:max_lights])
    return results


def track_sources(detections, max_tracks=1, max_jump=.1, smoothing=.6, aspect=1.):
    """Offline, bidirectional tracking seeded at the clearest observation.

    Low-area fragments contribute less position evidence. Hidden intervals
    interpolate between reliable observations; endpoint gaps coast on the
    last reliable motion. Brightness is not inferred from that interpolation.
    """
    out = [[] for _ in detections]
    available = [list(frame) for frame in detections]
    for tid in range(max_tracks):
        seeds = [(d.get('energy',d.get('brightness',1.)),i,j,d)
                 for i,frame in enumerate(available) for j,d in enumerate(frame)]
        if not seeds: break
        # Start from the first visible frame. The largest region anywhere in
        # the clip may be a late foreground reflection, not the chosen sun.
        seed_frame = min(p[1] for p in seeds)
        _, _, _, seed = max((p for p in seeds if p[1] == seed_frame),key=lambda p:p[0])
        ref = max(seed.get('energy',1.),1e-12)
        path = {seed_frame:(seed['u'],seed['v'],seed,1.)}
        for direction in (-1,1):
            u,v = seed['u'],seed['v']; vu=vv=0.; missed=0; recovery=[]
            for i in range(seed_frame+direction, len(out) if direction>0 else -1, direction):
                pu,pv = u+vu,v+vv
                # Bounded gate: a prolonged cover must not eventually admit
                # every lamp in the frame. max_jump is in image-height units.
                gate = max(max_jump,1e-4)
                options = []
                for d in available[i]:
                    dist = math.hypot((d['u']-pu)*aspect,d['v']-pv)
                    if dist <= gate:
                        ratio = min(d.get('energy',ref)/ref,1.)
                        options.append((dist/gate + .5*(1-ratio),d,ratio))
                if options:
                    _,d,ratio = min(options,key=lambda t:t[0])
                    # A crescent or canopy hole is evidence of visibility,
                    # not evidence that the source centre moved to that hole.
                    recovery.append(d)
                    recovery=recovery[-3:]
                    stable_recovery = (missed >= 3 and ratio >= .08 and len(recovery)==3
                        and all(math.hypot((q['u']-pu)*aspect,q['v']-pv)<gate*.4 for q in recovery))
                    if ratio >= .65 or stable_recovery:
                        gain=1. if ratio>=.65 else .25
                        nu,nv=pu+gain*(d['u']-pu),pv+gain*(d['v']-pv)
                        vu=.6*vu+.4*(nu-u);vv=.6*vv+.4*(nv-v)
                        u,v=nu,nv;missed=0
                        path[i]=(u,v,d,ratio)
                        continue
                else:
                    recovery=[]
                u,v=pu,pv;missed+=1
                if missed>20: vu*=.95;vv*=.95
        known=sorted(path)
        positions=[]
        cursor=0
        for i in range(len(out)):
            while cursor+1<len(known) and known[cursor+1]<=i: cursor+=1
            left=known[cursor]
            right=known[min(cursor+1,len(known)-1)]
            if i<known[0]: left,right=known[0],known[min(1,len(known)-1)]
            if i>known[-1]: left,right=known[max(0,len(known)-2)],known[-1]
            # Endpoint motion is unknowable for long hidden intervals.
            # Extrapolate at most three frames, then hold the prediction.
            sample_i = max(left-3,min(right+3,i))
            t=(sample_i-left)/(right-left) if left!=right else 0.
            u=path[left][0]+t*(path[right][0]-path[left][0])
            v=path[left][1]+t*(path[right][1]-path[left][1])
            positions.append((u,v))
        us=smooth_series([p[0] for p in positions],min(smoothing,.6))
        vs=smooth_series([p[1] for p in positions],min(smoothing,.6))
        for i,(u,v) in enumerate(zip(us,vs)):
            out[i].append(dict(u=u,v=v,brightness=seed.get('brightness',1.),tid=tid,
                               source_u=positions[i][0], source_v=positions[i][1],
                               confidence=path[i][3] if i in path else 0.))
            # Remove this source and nearby fragments before solving another.
            available[i]=[d for d in available[i]
                          if math.hypot((d['u']-u)*aspect,d['v']-v)>max(max_jump,seed.get('radius',0)*2)]
    return out
