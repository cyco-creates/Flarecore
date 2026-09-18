"""Read-only trace/ROI diagnostic for the QA's stopped-down dropout."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import torch
from flare.lens_lab import validate_settings,model_for,ghost_pairs,fitted_pupils,trace
from flare.lens_finish import spectral_rgb_weights
torch.set_num_threads(1)
cfg=validate_settings(dict(f_stop=16,exposure=9,source_glow=0,source_rays=0))
pairs=ghost_pairs();model=model_for(cfg)
for frame in [1,3,7,11]:
    t=frame/119;u=-.1+1.2*t;v=.1+.8*t
    pupil,area=fitted_pupils(model,cfg,u,v,16/9,pairs,96,'cuda',torch.float32)
    sensor,energy,_,clearance=trace(model,cfg,u,v,16/9,pupil,pairs,footprint=True,spectral=True)
    rgb=energy@spectral_rgb_weights('cuda',torch.float32)
    x=(.5-sensor[...,0]/36)*1024-.5;y=(.5-sensor[...,1]/(36*9/16))*576-.5
    good=(x>410)&(x<454)&(y>242)&(y<284)&(clearance>0)
    flux=(torch.where(good,rgb[...,2],0).sum(1)*area).cpu()
    ids=flux.argsort(descending=True)[:8]
    print(frame,[(pairs[i],float(flux[i]),int(good[i].sum())) for i in ids],flush=True)
