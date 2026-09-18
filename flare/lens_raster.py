# SPDX-License-Identifier: Apache-2.0
"""Exact box-filter coverage of projected pupil triangles.

The aperture boundary is a linearly interpolated signed field on each traced
triangle. Clip that field and all four pixel edges before integrating linear
irradiance. Narrow slivers consequently retain their area instead of flickering
between zero and one coverage sample. No image-space blur is used here.
"""
import torch


def pixel_integral(points, flux, margin, px, py, valid):
    """[triangle,pixel,channel] integral, with bounded caller fragment batches."""
    triangles, pixels = px.shape
    channels = flux.shape[-1]
    # Relative pixel coordinates avoid cancellation in polygon-area sums on
    # high-resolution frames. Nine slots suffice: triangle + five clip planes.
    attributes = channels + 3
    vertices = torch.zeros(triangles,pixels,9,attributes,device=points.device,dtype=points.dtype)
    vertices[:,:,:3,:2] = points[:,None] - torch.stack((px,py),-1)[:,:,None]
    vertices[:,:,:3,2:2+channels] = flux[:,None]
    vertices[:,:,:3,-1] = margin[:,None]
    vertices = vertices.reshape(-1,9,attributes)
    count = torch.where(valid.flatten(),3,0)
    slots = torch.arange(9,device=points.device)[None]
    for plane in range(5):
        prev_id = torch.where(slots == 0,count[:,None]-1,slots-1).clamp_min(0)
        prev = vertices.gather(1,prev_id[...,None].expand(-1,-1,attributes))
        if plane == 4:
            d,dp = vertices[:,:,-1],prev[:,:,-1]
        else:
            axis=plane//2
            sign=1 if plane%2==0 else -1
            d,dp = .5+sign*vertices[:,:,axis],.5+sign*prev[:,:,axis]
        active = slots < count[:,None]
        inside,previous_inside = d >= 0, dp >= 0
        crossing = (inside != previous_inside) & active
        denominator = dp-d
        t = dp/torch.where(denominator.abs()>1e-20,denominator,torch.ones_like(denominator))
        intersection = prev + t.clamp(0,1)[...,None]*(vertices-prev)
        keep = torch.stack((crossing,inside & active),-1).reshape(-1,18)
        candidates = torch.stack((intersection,vertices),2).reshape(-1,18,attributes)
        destinations = (keep.long().cumsum(-1)-1).clamp(0,8)
        clipped = torch.zeros_like(vertices)
        clipped.scatter_add_(1,destinations[...,None].expand(-1,-1,attributes),
                             torch.where(keep[...,None],candidates,0))
        vertices=clipped
        count=keep.sum(-1)
    a=vertices[:,1:8,:2]-vertices[:,0:1,:2]
    b=vertices[:,2:9,:2]-vertices[:,0:1,:2]
    area=.5*(a[...,0]*b[...,1]-a[...,1]*b[...,0]).abs()
    area=torch.where(torch.arange(7,device=points.device)[None]<count[:,None]-2,area,0)
    mean_flux=(vertices[:,0:1,2:2+channels]+vertices[:,1:8,2:2+channels]+vertices[:,2:9,2:2+channels])/3
    return (area[...,None]*mean_flux).sum(1).reshape(triangles,pixels,channels)
