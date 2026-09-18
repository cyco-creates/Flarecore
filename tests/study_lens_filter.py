# SPDX-License-Identifier: Apache-2.0
"""Test exact coverage without the legacy splat-era postfilter; never normalizes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import torch,numpy as np
from render_lens_candidate import Evidence,save_image
import flare.lens_lab as lens
torch.set_num_threads(1)
e=Evidence(sys.argv[1],'cuda');original=lens._reconstruct;convolve=lens.F.conv2d
def exact(*args,**kw):return original(*args,**kw,tiny_limit=0.)
lens._reconstruct=exact
e.meta['overrides']='tiny_limit=0, filter either current binomial or identity (exact area coverage itself antialiases). Fixed exposure. Source finish off.'
try:
    for pair in ['1:4','2:6','0:12','11:12','2:7']:
        cfg=dict(source_glow=0,source_rays=0,disabled_pairs=[f'{a}:{b}' for a,b in lens.ghost_pairs() if f'{a}:{b}'!=pair])
        for filter_name in ['binomial','coverage-only']:
            lens.F.conv2d=convolve if filter_name=='binomial' else lambda plane,*a,**k:plane
            for res in [1,2]:
                name=f'{pair.replace(":","-")}-{filter_name}-{res}x'
                a=e.render(name,cfg,h=1080*res,w=1920*res,grid=384)
                e.items[-1].update(tiny_limit_override=0.,filter_override=filter_name);e.flush()
                if res==2:
                    reduced=a.reshape(1080,2,1920,2,3).mean((1,3));np.save(e.folder/(name+'-reduced.npy'),reduced);save_image(e.folder/(name+'-reduced.png'),reduced)
finally:lens._reconstruct=original;lens.F.conv2d=convolve
