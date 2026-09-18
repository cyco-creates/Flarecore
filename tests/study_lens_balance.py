# SPDX-License-Identifier: Apache-2.0
"""Bounded second hierarchy study; the underlying traced geometry is unchanged."""
import argparse,sys,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import torch,numpy as np
from render_lens_candidate import Evidence,save_image,stats
from study_lens_envelope import envelope
from render_lens_plates import SOURCES
from flare.lens_lab import validate_settings

p=argparse.ArgumentParser();p.add_argument('--assets',required=True);p.add_argument('--output',required=True)
a=p.parse_args();torch.set_num_threads(1);e=Evidence(a.output,'cuda')
e.meta['study_code']={str(path.name):hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__),Path(__file__).with_name('study_lens_envelope.py')]}
e.meta['note']='Selective interface balance, all paths active. Source A versus angular-fill variant C, unshipped. Both strong and restrained scale the complete pass. Same raw plate data as v8-plates; see their PHOTO_CREDITS.md.'
surfaces={'0':{'reflection':.55},'1':{'reflection':.35},'2':{'reflection':.55},'4':{'reflection':.08},'6':{'reflection':.10},'11':{'reflection':.06}}
for name in ['black','lantern','sunset','tree-edge']:
    s=dict(u=.7,v=.3,color=[1.,1.,1.]) if name=='black' else SOURCES[name]
    lights=[dict(u=s['u'],v=s['v'],color=s['color'])]
    cfg=validate_settings(dict(source_glow=0,source_rays=0,surfaces=surfaces))
    ghost=e.render(name+'-trace',cfg,lights,h=1080,w=1920)
    plate=np.zeros_like(ghost) if name=='black' else np.load(Path(a.assets)/f'v8-plates/{name}-plate.npy')
    for label,variant in [('a',0),('c',2)]:
        scfg=validate_settings(dict(source_size=.008 if name=='black' else .011,source_rays=1 if name=='black' else .5))
        finish=envelope(scfg,s['u'],s['v'],1080,1920,'cuda',torch.float32,variant=variant).cpu().numpy()*np.array(s['color'],np.float32)
        save_image(e.folder/f'{name}-source-{label}.png',finish);np.save(e.folder/f'{name}-source-{label}.npy',finish)
        for level,gain in [('restrained',.65),('strong',2.6)]:
            key=f'{name}-{label}-{level}';out=(ghost+finish)*gain
            np.save(e.folder/(key+'.npy'),out);save_image(e.folder/(key+'.png'),out)
            if name!='black':save_image(e.folder/(key+'-composite.png'),plate+out)
            e.meta.setdefault('composed_studies',[]).append(dict(name=key,ghost_item=name+'-trace',source_settings=scfg,source_function_variant=variant,
                source_color=s['color'],u=s['u'],v=s['v'],gain=gain,plate=None if name=='black' else str(Path(a.assets)/f'v8-plates/{name}-plate.npy'),**stats(out)))
    e.flush()
e.sheet([f'{name}-c-{level}'+('' if name=='black' else '-composite') for name in ['black','lantern','sunset','tree-edge'] for level in ['restrained','strong']],cols=2)
