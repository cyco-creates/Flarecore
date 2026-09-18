"""Read-only image analysis with generated QA reports; no automatic quality score."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image,ImageDraw
from render_lens_candidate import stats


def main():
    p=argparse.ArgumentParser();p.add_argument('folder');args=p.parse_args()
    folder=Path(args.folder);manifest=json.loads((folder/'manifest.json').read_text())
    output=dict(source_manifest_sha256=__import__('hashlib').sha256((folder/'manifest.json').read_bytes()).hexdigest(),
                method='Raw linear RGB; luminance weights .2126,.7152,.0722. Global centroid translation alignment is only a diagnostic, not a full optical-flow model or an automatic flicker pass.',sequences={})
    modes=sorted(set(i['name'].rsplit('-',1)[0] for i in manifest['items']))
    torch.set_num_threads(1)
    for mode in modes:
        entries=sorted([i for i in manifest['items'] if i['name'].rsplit('-',1)[0]==mode],key=lambda x:x['name'])
        prev=None;first=None;metrics=[]
        for entry in entries:
            a=np.load(folder/(entry['name']+'.npy'))
            s=stats(a);s['name']=entry['name']
            if first is None:first=a.copy()
            if prev is not None:
                h,w=a.shape[:2];dx=s['centroid'][0]-last['centroid'][0];dy=s['centroid'][1]-last['centroid'][1]
                tensor=torch.from_numpy(prev).permute(2,0,1)[None]
                transform=torch.tensor([[[1,0,-2*dx/w],[0,1,-2*dy/h]]],dtype=torch.float32)
                aligned=F.grid_sample(tensor,F.affine_grid(transform,tensor.shape,align_corners=False),align_corners=False)[0].permute(1,2,0).numpy()
                s['raw_l1']=float(np.abs(a-prev).sum()/max(float(a.sum()),1e-12))
                s['centroid_aligned_l1']=float(np.abs(a-aligned).sum()/max(float(a.sum()),1e-12))
            if mode=='static':s['max_rgb_difference_from_first']=float(np.abs(a-first).max())
            metrics.append(s);prev=a;last=s
        for i in range(1,len(metrics)-1):
            midpoint=(metrics[i-1]['energy']+metrics[i+1]['energy'])*.5
            metrics[i]['energy_local_residual']=(metrics[i]['energy']-midpoint)/max(midpoint,1e-12)
        flagged=sorted([m for m in metrics if 'energy_local_residual'in m],key=lambda m:-abs(m['energy_local_residual']))[:8]
        output['sequences'][mode]=dict(frames=len(entries),metrics=metrics,largest_energy_events=flagged,
                                      max_static_difference=max((m.get('max_rgb_difference_from_first',0) for m in metrics),default=0))
        sheet=Image.new('RGB',(10*192,((len(entries)+9)//10)*124),(15,17,22));draw=ImageDraw.Draw(sheet)
        for i,entry in enumerate(entries):
            image=Image.open(folder/(entry['name']+'.png'));image.thumbnail((192,108))
            x=i%10*192;y=i//10*124;sheet.paste(image,(x,y));draw.text((x+3,y+109),entry['name'],fill='white')
        sheet.save(folder/(mode+'-every-frame.jpg'),quality=95)
    (folder/'temporal-metrics.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps({mode:dict(frames=d['frames'],max_static_difference=d['max_static_difference'],largest_energy_events=[(m['name'],m['energy_local_residual']) for m in d['largest_energy_events']]) for mode,d in output['sequences'].items()},indent=2))


if __name__=='__main__':main()
