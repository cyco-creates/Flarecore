# SPDX-License-Identifier: Apache-2.0
import copy
import math
import pytest
import torch
from flare.source_track import detect_sources, track_sources
from flare.visibility import apply_source_visibility
from flare.occlude import occlusion_factor
from test_nodes import run_node


def covered_clip():
    clip = torch.full((5,64,96,3), .15)
    yy,xx=torch.meshgrid(torch.arange(64),torch.arange(96),indexing='ij')
    disc=(xx-48)**2+(yy-32)**2 <= 8**2
    clip[:,disc]=1.
    clip[1,:,48:]=.15
    clip[2]=.15
    clip[3,:,48:]=.15
    return clip


def measure(clip, chunk=2, mode='image', radius=.125, **light):
    lights=[[dict(u=.5,v=.5,brightness=1.,tid=0,**light)] for _ in clip]
    apply_source_visibility(lambda a,b:clip[a:b], lights,chunk,radius,mode,0.)
    return lights


def test_partial_cover_dims_even_when_peak_is_still_saturated():
    clip=covered_clip(); tr=measure(clip)
    v=[p[0]['source_visibility'] for p in tr]
    assert clip[0].max()==clip[1].max()==1
    assert v[0]>.99 and .3<v[1]<.65 and v[2]==0 and v[4]>.99


def test_chunk_invariance_and_no_mutation_of_images():
    clip=covered_clip(); original=clip.clone()
    assert measure(clip,1)==measure(clip,5)
    assert torch.equal(clip,original)


def test_hybrid_does_not_double_count_obstruction():
    p=measure(covered_clip(),mode='hybrid',occlusion=.5)[1][0]
    assert .5<=p['occlusion']<.7


def test_off_frame_and_black_reference_are_unknown_not_blocked():
    assert all(f[0].get('occlusion',0)==0 for f in measure(torch.zeros(4,32,32,3)))
    tr=[[dict(u=-.1,v=.5)] for _ in range(3)]
    apply_source_visibility(lambda a,b:torch.zeros(b-a,32,32,3),tr,2,.02)
    assert all(f[0].get('occlusion',0)==0 for f in tr)


def test_thin_branch_does_not_disappear_in_a_coverage_dead_zone():
    depth=torch.zeros(128,128);depth[:,64]=1.
    assert .005<occlusion_factor(depth,.5,.5,radius=.15)<.1


def test_clipped_plateau_has_one_centre_not_many_rim_candidates():
    clip=torch.zeros(3,96,160,3);clip[:,30:60,60:100]=1.
    det=detect_sources(clip,.6)
    assert all(len(f)==1 for f in det)
    assert det[0][0]['u']==pytest.approx(.5)
    assert det[0][0]['v']==pytest.approx(45/96)


def test_hidden_path_interpolates_without_jumping_to_a_rival():
    det=[]
    for i in range(30):
        source=[] if 10<=i<20 else [dict(u=.2+i*.01,v=.4,energy=1.,brightness=1.)]
        det.append(source+[dict(u=.85,v=.6,energy=.2,brightness=1.)])
    tr=track_sources(det,max_jump=.04,smoothing=0,aspect=2)
    for i,frame in enumerate(tr):
        assert frame[0]['u']==pytest.approx(.2+i*.01,abs=.001)
        if 10<=i<20: assert frame[0]['confidence']==0


def test_a_missing_endpoint_does_not_extrapolate_across_the_frame():
    det=[[dict(u=.2+i*.01,v=.5,energy=1.)] if i<5 else [] for i in range(90)]
    tr=track_sources(det,max_jump=.04,smoothing=0)
    assert tr[-1][0]['u']<.3


def test_source_tracker_keeps_two_distinct_lights():
    det=[[dict(u=.2+i*.005,v=.3,energy=1.),dict(u=.8-i*.005,v=.7,energy=.6)] for i in range(12)]
    tr=track_sources(det,max_tracks=2,smoothing=0,max_jump=.04)
    assert all(len(f)==2 for f in tr)
    assert tr[-1][0]['u']<tr[-1][1]['u']


def test_render_visibility_dims_shrinks_and_recovers_without_depth():
    clip=covered_clip()
    _,out,_=run_node(clip,light_x=.5,light_y=.5,occlusion_radius=.125,
                     colorspace='linear',visibility_mode='image',occlusion_smooth=0)
    energy=out.sum((1,2,3))
    assert 0<energy[1]<energy[0]*.7
    assert energy[2]==0 and torch.allclose(out[0],out[4])
    # Compare normalized second spatial moments: shrinking, not just dimming.
    yy,xx=torch.meshgrid(torch.arange(64),torch.arange(96),indexing='ij')
    radius=(xx-48)**2+(yy-32)**2
    moment=lambda frame: (frame.sum(-1)*radius).sum()/frame.sum()
    assert moment(out[1])<moment(out[0])


def test_visibility_mode_validation():
    with pytest.raises(ValueError,match='visibility_mode'):
        run_node(torch.zeros(1,32,32,3),visibility_mode='typo')


def test_default_hybrid_does_not_make_a_held_hidden_light_shine():
    clip=torch.zeros(10,60,100,3);clip[:4,25:31,40:46]=1.
    _,flare,_=run_node(clip,position_mode='track',detect_threshold=.5,
                       track_hold=30,track_fade=1)
    assert flare[2].sum()>0
    assert flare[6].sum()==0


def test_status_distinguishes_visibility_from_uncertain_position():
    from test_nodes import FlareRender
    text=FlareRender._source_status([[dict(occlusion=.7,confidence=.1)]], 'hybrid')
    assert '30%' in text and '1 of 1 frames' in text
