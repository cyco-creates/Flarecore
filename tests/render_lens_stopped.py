"""Current-code stopped-down temporal replacement after the scouting fix."""
import argparse
import hashlib
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import torch
from render_lens_candidate import Evidence
from flare.lens_lab import validate_settings
p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
torch.set_num_threads(1);e=Evidence(args.output,'cuda')
e.meta['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
c=validate_settings(dict(quality='standard',f_stop=16,exposure=9))
for f in range(120):
    t=f/119;e.render(f'diagonal-stopped-{f:03d}',c,[dict(u=-.1+1.2*t,v=.1+.8*t)],h=576,w=1024)
for f in range(16):e.render(f'static-{f:02d}',validate_settings({}),h=576,w=1024,grid=96)
e.sheet([i['name'] for i in e.items[::10]],cols=3);e.meta['fps']=30;e.flush()
