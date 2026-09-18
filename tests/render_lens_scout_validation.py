"""Narrow-pupil regression and unchanged f/4 branch evidence."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import torch
from render_lens_candidate import Evidence
from flare.lens_lab import validate_settings,ghost_pairs
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--assets',required=True);args=p.parse_args()
torch.set_num_threads(1);e=Evidence(args.output,'cuda')
e.meta['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
c=validate_settings(dict(source_glow=0,source_rays=0))
current=e.render('unchanged-f4-offaxis',c,h=1080,w=1920)
old_path=Path(args.assets)/'v12-stills/offaxis.npy';old=np.load(old_path)
e.meta['f4_equivalence']=dict(reference=str(old_path),reference_sha256=hashlib.sha256(old_path.read_bytes()).hexdigest(),
    max_rgb_difference=float(np.abs(old-current).max()),
    note='Scouting remains48 for f/4 and wider; other optical code and settings are unchanged. This matched render verifies the unchanged branch, not arbitrary f-number equivalence.')
assert np.allclose(current,old,atol=1e-6,rtol=1e-5)
for stop in [8,16,22]:e.render(f'f{stop}',dict(c,f_stop=stop),h=1080,w=1920)
cfg=validate_settings(dict(c,f_stop=16,exposure=9,disabled_pairs=[f'{a}:{b}' for a,b in ghost_pairs() if (a,b)!=(0,2)]))
for frame in [3,7]:
    t=frame/119
    for grid in [48,96,192,384]:e.render(f'small-ghost-f{frame:02d}-n{grid}',cfg,[dict(u=-.1+1.2*t,v=.1+.8*t)],h=576,w=1024,grid=grid)
for frame in range(48):
    t=3/119;u=-.1+1.2*t+(frame-23.5)*.0001
    e.render(f'small-ghost-micro-{frame:03d}',cfg,[dict(u=u,v=.1+.8*t)],h=576,w=1024,grid=96)
e.sheet([i['name'] for i in e.items[:12]],cols=3);e.flush()
