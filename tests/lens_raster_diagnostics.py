"""Opt-in isolated reconstruction-branch comparison; writes only QA artifacts."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import json
import argparse
import torch
from render_lens_candidate import Evidence
import flare.lens_lab as lens
torch.set_num_threads(1)
p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--grid',type=int,default=192);p.add_argument('--pair',default='11:12');p.add_argument('--f-stop',type=float,default=4)
args=p.parse_args();e=Evidence(args.output,'cuda')
original=lens._reconstruct
e.meta['experiment']='Only tiny-footprint cutoff is overridden; same renderer/settings otherwise.'
for limit in [.5,.1,0.]:
    def reconstruct(*a,**kw):return original(*a,**kw,tiny_limit=limit)
    lens._reconstruct=reconstruct
    cfg=dict(source_glow=0,source_rays=0,f_stop=args.f_stop,disabled_pairs=[f'{a}:{b}' for a,b in lens.ghost_pairs() if f'{a}:{b}'!=args.pair])
    e.render(f'threshold-{limit}',cfg,h=1080,w=1920,grid=args.grid)
    e.items[-1]['tiny_limit_override']=limit;e.flush()
lens._reconstruct=original
