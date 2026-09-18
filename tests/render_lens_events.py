# SPDX-License-Identifier: Apache-2.0
"""Targeted prior-QA temporal regressions, not a beauty-normalized showcase."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import torch
from render_lens_candidate import Evidence
from flare.lens_lab import validate_settings

p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
torch.set_num_threads(1);e=Evidence(args.output,'cuda')
e.meta['generator_sha256']=__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest()
c=validate_settings(dict(f_stop=16,exposure=9,source_glow=0,source_rays=0,quality='standard'))
for f in range(16):
    t=f/119;e.render(f'entry-{f:03d}',c,[dict(u=-.1+1.2*t,v=.1+.8*t)],h=576,w=1024)
for f in [48,49,50,68,69,70]:
    t=f/119
    for grid in [96,192]:e.render(f'contour-{f:03d}-n{grid}',c,[dict(u=-.1+1.2*t,v=.1+.8*t)],h=576,w=1024,grid=grid)
for grid in [96,192]:
    for label,offset in [('left',-.0001),('axis',0),('right',.0001)]:
        e.render(f'axis-{label}-n{grid}',dict(c,f_stop=4,exposure=6),[dict(u=.5+offset,v=.5)],h=576,w=1024,grid=grid)
e.sheet([i['name'] for i in e.items],cols=4)
