# SPDX-License-Identifier: Apache-2.0
"""Authored polynomial pupil transport for artistic ghost caustics.

This maps a uniformly illuminated pupil through a low-order polynomial. It
forms folds by overlapping samples instead of drawing a bright polygon rim.
It is NOT a commercial optical prescription or a replacement for Lens Lab.
"""
import math
from functools import lru_cache

import numpy as np
import torch
import torch.nn.functional as F


@lru_cache(maxsize=1)
def _samples():
    n=1024
    rng=np.random.default_rng(73193)
    y,x=np.mgrid[:n,:n].astype(np.float32)
    # Fixed stratification avoids regular grid bands without temporal noise.
    x=(x+rng.random(x.shape,dtype=np.float32))*2/n-1
    y=(y+rng.random(y.shape,dtype=np.float32))*2/n-1
    return x.ravel(),y.ravel()


@lru_cache(maxsize=32)
def _plate(amount,coma,blades,roundness,seed,trefoil_amount):
    x,y=_samples()
    radius=np.sqrt(x*x+y*y)
    phi=np.arctan2(y,x)
    sector=2*math.pi/blades
    folded=(phi+sector/2)%sector-sector/2
    edge=math.cos(math.pi/blades)/np.cos(folded)
    edge=edge+roundness*(1-edge)
    inside=radius<edge
    x,y=x[inside],y[inside]
    r2=x*x+y*y
    # Defocus / spherical fold plus trefoil and asymmetric coma. The outer
    # pupil and the concentrated inner caustic share the same transport.
    radial=(1-amount)+amount*(-.64+1.18*r2)
    trefoil=.19*amount*trefoil_amount
    px=x*radial+trefoil*(x*x-y*y)+.18*coma*(3*x*x+y*y)
    py=y*radial-2*trefoil*x*y+.36*coma*x*y
    # Texture spans [-1.5,+1.5] in element coordinates. Do not normalize by a
    # frame maximum: the fixed flux budget keeps source motion meaningful.
    size=320
    tx=(px/3+.5)*size-.5;ty=(py/3+.5)*size-.5
    ix=np.floor(tx).astype(np.int32);iy=np.floor(ty).astype(np.int32)
    fx=tx-ix;fy=ty-iy
    phase=(int(seed)%997)*.013
    illumination=.96+.025*np.sin(x*17+y*9+phase)+.015*np.cos(x*43+y*31+phase)
    plate=np.zeros(size*size,dtype=np.float64)
    gain=(size*size/9)*(4/(1024*1024))
    for dx,dy,w in [(0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)]:
        xx=ix+dx;yy=iy+dy
        valid=(xx>=0)&(xx<size)&(yy>=0)&(yy<size)
        plate+=np.bincount((yy[valid]*size+xx[valid]),weights=(w*illumination)[valid]*gain,minlength=size*size)
    tex=torch.from_numpy(plate.astype(np.float32).reshape(1,1,size,size))
    # Finite source footprint; integrated splats plus a compact low-pass avoid
    # singular fireflies, while retaining the actual curved fold structure.
    mips=[F.avg_pool2d(tex,3,stride=1,padding=1)]
    while mips[-1].shape[-1]>5:
        mips.append(F.avg_pool2d(mips[-1],2))
    return tuple(mips)


def pupil_caustic(u,v,p):
    mips=_plate(float(p['caustic']),float(p.get('coma',0)),
                 max(3,int(p['blades'])),float(p.get('roundness',0)),int(p.get('seed',0)),float(p.get('trefoil',0)))
    dtype=u.dtype
    # CPU half grid_sample is unsupported, and far-off half coordinates can
    # overflow during transforms. Outside this bounded plate there is no light.
    grid=torch.stack((u.float().clamp(-6,6)/1.5,v.float().clamp(-6,6)/1.5),-1)[None]
    lod=min(len(mips)-1,max(0,math.log2(max(1,float(p.get('_px',0))*320/3))))
    lo=int(lod);hi=min(lo+1,len(mips)-1);blend=lod-lo
    sampled=F.grid_sample(mips[lo].to(u.device),grid,align_corners=False,padding_mode='zeros')[0,0]
    if blend:
        upper=F.grid_sample(mips[hi].to(u.device),grid,align_corners=False,padding_mode='zeros')[0,0]
        sampled=sampled*(1-blend)+upper*blend
    return sampled.to(dtype)
