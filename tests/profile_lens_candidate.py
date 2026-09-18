"""Opt-in timing probe, standalone only; does not change runtime settings."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
import flare.lens_lab as lens
torch.set_num_threads(1)
parts = {}
for name in ['trace', 'fitted_pupils', '_reconstruct']:
    original = getattr(lens, name)
    def wrapped(*args, _f=original, _n=name, **kwargs):
        torch.cuda.synchronize()
        t=time.perf_counter()
        result=_f(*args, **kwargs)
        torch.cuda.synchronize()
        parts[_n]=parts.get(_n,0)+time.perf_counter()-t
        return result
    setattr(lens,name,wrapped)
for device in ['cuda','cpu']:
    parts.clear();t=time.perf_counter()
    lens.render_lens({'quality':'standard'},[{'u':.7,'v':.3}],360,640,device)
    print(device,time.perf_counter()-t,parts,flush=True)
