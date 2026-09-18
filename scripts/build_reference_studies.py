"""Authored procedural studies from labelled moving-light reference tests.

Pure builder: prints JSON for review/patching, never overwrites preset files.
Source plates remain private research material outside this repository.
These are visual approximations, not recovered commercial lens prescriptions.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def curve(target, points, driver='radius'):
    return dict(target=target, driver=driver, points=points, interpolation='smooth')


def response(e, **channels):
    e = copy.deepcopy(e)
    existing = {c['target']: c for c in e.get('motion', {}).get('channels', [])}
    for target, points in channels.items():
        existing[target] = curve(target, points)
    e['motion'] = dict(enabled=True, channels=list(existing.values()))
    return e


def element(kind, label, offset, scale, energy, color, **kw):
    e = dict(type=kind, label=label, offset=offset, scale=scale,
             intensity=energy, color=color, auto_rotate=False)
    e.update(kw)
    e['slot'] = dict(glow='glows', glint='rays', iris='ghosts',
                     ring='rings', hoop='hoops', streak='streaks',
                     spectral='caustics', orbs='ghosts')[kind]
    # Ghosts remain briefly visible after the source crosses the picture edge.
    return response(e, opacity=[[0,.35],[.4,.8],[.9,1],[1.8,.85],[2.5,.18],[3.6,0]])


def source(halo=.3, rays=.3, warmth=(.82,.92,1), size=1):
    out = [
        element('glow','Source · white core',0,.075*size,7,[1,1,1],params=dict(softness=.35,falloff=1.8)),
        element('glow','Source · scatter halo',0,.65*size,halo*.9,list(warmth),params=dict(softness=.35,falloff=1.65),irregular=.22),
        element('glint','Source · fine diffraction',0,.30*size,rays*.8,list(warmth),params=dict(points=52,length=.68,thickness=.012,length_jitter=.55,ray_falloff=2.1,fan=.025),irregular=.6,rotation=13),
        element('glint','Source · long rays',0,.40*size,rays*.08,list(warmth),params=dict(points=12,length=.65,thickness=.004,length_jitter=.6,ray_falloff=1.7,fan=.008),irregular=.5,rotation=23),
    ]
    for e in out:
        # Source finish and secondary reflections have different visibility.
        e['motion'] = dict(enabled=True, channels=[curve('opacity',
            [[-.25,0],[-.03,.12],[.07,.9],[.25,1],[1,1]], 'edge')])
    return out


def disc(label, offset, scale, energy, color, stretch=(1,1), rim=.2, **kw):
    fill = element('iris',label,offset,scale,energy,color,stretch=list(stretch),
                   params=dict(blades=9,roundness=.9,edge_softness=.25,surface_detail=.12,density_bias=.48),irregular=.14,**kw)
    edge = copy.deepcopy(fill)
    edge.update(label=label+' · rim', intensity=energy*rim)
    edge['params'].update(hollow=.94,edge_softness=.08,density_bias=0)
    return [fill, edge]


def line(label, offset, scale, energy, color, thickness=.009, **kw):
    return element('streak',label,offset,scale,energy,color,
                   params=dict(length=.9,thickness=thickness,curve=.03),**kw)


def ring(label, offset, scale, energy, color, thickness=.03, **kw):
    return element('ring',label,offset,scale,energy,color,
                   params=dict(radius=1,thickness=thickness,surface_detail=.12),**kw)


def vertical_axis(e, offset):
    """Independent anamorphic Y mapping; never pin a moving source to a test row."""
    e=copy.deepcopy(e);e['move']=[1,0];e['shift']=[0,0]
    limit=min(1.5,4/max(abs(1-offset),1e-9))
    c=curve('shift_y',[[-limit,-limit*(1-offset)],[limit,limit*(1-offset)]],'y')
    c['interpolation']='linear'
    channels=e.setdefault('motion',dict(enabled=True,channels=[]))['channels']
    channels[:]=[old for old in channels if old['target']!='shift_y']+[c]
    return e


def study(key, title, category, focal, stop, pair, side, elements, notes):
    for i,e in enumerate(elements):
        e['id'] = f'{key}-{i:02d}'
    return dict(key=key, reference=dict(lens=title,focal_mm=focal,t_stop=stop,
                pair=pair,side=side,notes=notes), preset=dict(schema_version=1,
                name=f'{title} {focal}mm T{stop} · study',author='Flarecore',
                category=category,subcategory='Reference studies · '+('Anamorphic' if category=='Anamorphic' else 'Spherical'),
                global_=None, elements=elements))


def refine_anatomy(studies):
    """Visual revisions from the labelled sweeps, before bounded energy fit.

    Distinct source finishes, focused caustic substructure and nonlinear
    pupil changes are authored explicitly. None imply measured glass data.
    """
    for study in studies:
        key=study['key'];es=study['preset']['elements']
        def selected(prefix):
            return [e for e in es if e['label'].startswith(prefix)]
        def add(e):
            e['id']=f'{key}-{len(es):02d}';es.append(e)
        def focus(label,offset,scale,color,energy=.1,points=3):
            add(element('iris',label,offset,scale,energy*.4,color,blur=.07,
                params=dict(blades=points,roundness=.18,hollow=.6,edge_softness=.5,density_bias=.6),
                irregular=.45,rotation=17,shade=.8,auto_rotate=True))
            add(element('glow',label+' · focus',offset,scale*.24,energy*.6,color,
                params=dict(softness=.55,falloff=1.8),irregular=.25))
        # A pupil's bright rim need not have an equally bright interior.
        for e in es:
            if e['type']=='iris' and not e['label'].endswith(' · rim'):
                e['shade']=.35
            if e['type']=='glow' and not e['label'].startswith('Source ·'):
                e['intensity']*=.65
            if e['label'].startswith('Source ·') and e['type']=='glint':
                e['params']['ray_taper']=.9
                e['blur']=.035
            if e['label'].startswith('Source · scatter'):
                e['intensity']*=1.6
        if key=='kowa_cine_prominar':
            for e in selected('Front gold oval'):e['scale']*=1.65
            for e in selected('Rear disc'):
                e['blur']=.025;e['intensity']*=.7
            for e in selected('Focused cyan caustic'):
                e['scale']*=.7;e['stretch']=[1,.6];e['params']['thickness']=.15
            focus('Focused cyan knot',2.04,.11,[.2,.7,1],.12,5)
            for t in [.73,1.1,1.4]:focus('Uneven microghost scatter',t,.035,[1,.78,.33],.045,5)
        elif key=='cooke_anamorphic_sf':
            for e in selected('Source · fine'):
                e['params'].update(points=8,thickness=.045,fan=.01);e['rotation']=0;e['scale']=.23;e['intensity']*=.6
            for e in selected('Source · long'):
                e['params'].update(points=4,length=.7);e['rotation']=0
            for e in selected('Flattened blue ghost'):
                e['stretch']=[3.2,.72];e['intensity']*=1.6
            for e in selected('Quiet upright ghost'):
                e['blur']=.1;e['intensity']*=.65
            for j,(dy,g,s) in enumerate([(.025,.05,.60),(.052,.024,.51),(.085,.009,.41)]):
                l=vertical_axis(line(f'Layered blue skirt {j+1}',.55,s,g,[.02,.12,1],.024),0)
                l['shift'][1]=dy;add(l)
            add(element('glow','Subtle blue veiling',.6,2.5,.006,[.08,.28,.5],params=dict(softness=.8,falloff=2)))
            add(line('Source · vertical blue glint',0,.45,.025,[.3,.65,1],.008,rotation=90))
        elif key=='arri_signature':
            for e in selected('Sparse coated ghost'):
                e['intensity']*=.3;e['blur']=.03
            focus('Blue focused reflection',1.65,.11,[.08,.25,1],.06,5)
            focus('Far blue folded reflection',2.32,.12,[.1,.12,1],.012,3)
        elif key=='zeiss_radiance':
            for e in selected('Source · fine'):
                e['params'].update(points=18,length_jitter=.12,thickness=.025,fan=.025,ray_taper=.8)
                e['irregular']=.10;e['color']=[.86,1,.74];e['intensity']*=1.6
                e['scale']*=.85
            for e in selected('Front blue pupil'):
                e['intensity']*=1.45;e['params']['density_bias']=.1
            for e in selected('Rear focused pupil'):
                e['intensity']*=.4;e['params']['hollow']=.62
            focus('Focused blue pupil knot',1.98,.09,[.24,.62,1],.08,5)
        elif key=='atlas_orion':
            for e in selected('Lower compressed blue ghost'):
                e.update(vertical_axis(e,2));e['stretch']=[3.5,.25];e['blur']=.035
            for dy,g in [(-.018,.09),(.015,.06)]:
                l=vertical_axis(line('Blue double-line reflection',.9,1.8,g,[.015,.08,1],.0018),0)
                l['shift'][1]=dy;add(l)
            add(vertical_axis(line('Lower ghost vertical glint',1.3,.32,.011,[.06,.4,1],.009,rotation=90),2))
        elif key in ('panavision_c','cooke_speed_panchro'):
            for e in selected('Source · fine'):
                e['scale']*=1.5;e['params'].update(points=96,thickness=.009,length_jitter=.55,fan=.045,ray_taper=1)
                e['intensity']*=.45;e['irregular']=.8
            for e in selected('Source · scatter'):
                e['scale']*=1.45
            if key=='cooke_speed_panchro':
                for e in selected('Broad cool outer arc'):
                    e['scale']=1.5;e['params']['thickness']=.23
                    e['motion']['channels'].append(curve('scale',[[0,.88],[.45,.88],[1.3,1.3],[2.2,1.65]]))
            else:
                for e in selected('Opposite lavender oval'):
                    e['intensity']*=.6;e['params']['hollow']=.45;e['rotation']=-12
                focus('Small cyan focused edge',1.86,.07,[.18,.6,1],.06,4)
        elif key=='canon_k35':
            for e in selected('Front rose pupil')+selected('Near soft pupil'):
                e['blur']=.14;e['intensity']*=.55
            for e in selected('Far prismatic arc'):
                e['params'].update(completion=115,completion_feather=.5);e['scale']*=.65;e['auto_rotate']=True
            for e in selected('Violet rear disc'):
                e['params'].update(crescent=.32,density_bias=.75)
            focus('Green focal knot',1.84,.10,[.12,1,.48],.18,5)
            focus('Far spectral folded knot',2.45,.1,[.7,.75,1],.09,3)
        elif key=='leitz_hugo':
            for e in selected('Green focused cusp'):
                e['params']['density_bias']=.8;e['shade']=.7;e['blur']=.02
            for e in selected('Faint enclosing blue pupil'):e['intensity']*=.55
            for e in selected('Small front amber crescent'):e['intensity']*=1.8
        elif key=='arri_master_prime':
            for e in selected('Near-invisible opposite pupil'):e['intensity']*=.2
            for e in selected('Very faint blue outer arc'):e['intensity']*=.4
        elif key=='atlas_mercury':
            for e in selected('Lower rose reflection'):e['blur']=.1;e['intensity']*=.3
            for e in selected('Small opposite cyan point')+selected('Far white point'):
                e['intensity']*=3
        elif key=='hawk_vlite_vintage74':
            for e in selected('Blue veiling field'):e['intensity']*=.45
            for e in selected('Textured opposite rose oval'):
                e['scale']*=1.3;e['params'].update(edge_softness=.6,surface_detail=.22,density_bias=.6)
            for e in selected('Broad blue reflection arc'):
                e['intensity']*=2;e['params']['thickness']=.25
            add(vertical_axis(line('Opposite vertical reflection',1.8,.45,.04,[.4,.5,1],.008,rotation=90),0))
            add(vertical_axis(element('glow','Lower streak hotspot',1.65,.08,.10,[.7,.8,1],params=dict(softness=.5,falloff=2)),2))
        elif key=='panavision_primo_classic':
            for e in selected('Large green pupil'):e['intensity']*=.45
            for e in selected('Cyan focused oval'):
                e['params'].update(hollow=.6,density_bias=.6);e['shade']=.8
            for e in selected('Front companion pupil')+selected('Bright front pupil'):
                e['params'].update(blades=3,roundness=.35);e['blur']=.045
            focus('Rose focused cusp',1.66,.085,[1,.18,.5],.10,3)
        elif key=='arri_master_anamorphic':
            for e in selected('Faint upright violet pupil'):
                e['params']['density_bias']=-.8;e['blur']=.03
            add(vertical_axis(line('Vertical violet ghost glint',1.9,.31,.015,[.26,.11,1],.01,rotation=90),2))
        elif key=='zeiss_super_speed':
            for e in selected('Far violet pupil'):e['intensity']*=.45
            for e in selected('Triangular violet caustic'):
                e['scale']*=1.8;e['params'].update(hollow=.55,edge_softness=.35);e['intensity']*=2;e['blur']=.03
                e['auto_rotate']=True
            for e in selected('Clipped front white pupil'):
                e['blur']=.08
                e.update(response(e,opacity=[[0,0],[.5,0],[.9,.2],[1.3,1],[2.4,.3]]))


def refine_transport(studies):
    """Second-pass anatomy: source fibres, folded pupils and moving annuli.

    This is deliberately authored from visible test anatomy, not claimed as a
    fitted glass prescription. Stable IDs preserve saved element selection.
    """
    for s in studies:
        key=s['key'];es=s['preset']['elements']
        def pick(prefix):return [e for e in es if e['label'].startswith(prefix)]
        def folded(e,amount=.8,coma=.3,blades=3):
            e['type']='iris';e['slot']='caustics';e['auto_rotate']=True
            e['params']=dict(blades=blades,roundness=.95,edge_softness=.24,
                             caustic=amount,coma=coma,trefoil=.08,surface_detail=.65,hollow=0)
            e['blur']=.012;e['irregular']=.35
            e.update(response(e,caustic=[[0,.001],[.45,.001],[.85,amount],[1.7,amount]],
                              coma=[[0,0],[.4,0],[1.5,coma],[2.4,coma]]))
        for e in es:
            label=e['label']
            if label=='Source · white core':e['scale']*=.9
            elif label=='Source · scatter halo':
                e['params']['scatter']=.25;e['scale']*=.8;e['intensity']*=1.6
            elif label=='Source · fine diffraction':
                e['params']['ray_taper']=.8;e['scale']*=.7
                e['intensity']*=.6;e['blur']=.03
            elif label=='Source · long rays':
                e['params']['ray_taper']=.8;e['scale']*=.8;e['intensity']*=.3
            elif 'veiling' in label.lower() or label.endswith(' veil') or 'veiling' in label:
                e['intensity']*=.3
            if 'knot' in label.lower() and e['type']=='iris':
                folded(e,.8,.35);e['intensity']*=4
            if e['type']=='iris' and e['params'].get('surface_detail',0):
                e['params']['surface_detail']=.55
        if key=='kowa_cine_prominar':
            for e in pick('Front gold oval'):
                e['scale']*=.65;e['stretch']=[.7,1];e['params']['crescent']=.25
            for e in pick('Rear disc'):
                e['auto_rotate']=True;e['stretch']=[1,.82];e['shade']=.65
                e['params']['coma']=.28
                e['params']['density_bias']=.85
                e['intensity']*=1.5 if e['label'].endswith(' · rim') else .5
                e.update(response(e,stretch_x=[[0,1],[.45,1],[.85,1],[2.4,.8]],
                                  coma=[[0,0],[.45,0],[1.1,.28],[2.4,.55]]))
            for e in pick('Uneven microghost'):
                if e['type']=='iris':
                    folded(e,.8,.5,3);e['intensity']*=1.5
            for e in pick('Focused cyan caustic'):
                folded(e,.9,.5,3);e['scale']=.18;e['intensity']*=.55
            for e in pick('Focused cyan knot'):
                e['intensity']*=2;e['scale']*=1.15
            for e in pick('Blue outer reflection'):
                e['params'].update(thickness=.08,edge_bias=.7,surface_detail=.25)
                e['intensity']*=.85
        elif key=='zeiss_radiance':
            for e in pick('Source · scatter'):e['intensity']*=.4
            for e in pick('Source · fine'):e['intensity']*=3;e['params']['fan']=.04
            for e in pick('Front blue pupil'):
                e['scale']=1.05;e['stretch']=[1,1];e['params']['hollow']=0
                if e['label'].endswith(' · rim'):e['intensity']*=.12
                else:e['intensity']*=5
                e['params']['density_bias']=.05
                e.update(response(e,scale=[[0,1],[.45,1],[.9,1],[1.4,.3],[1.7,.1],[2.4,.1]]))
            for e in pick('Rear focused pupil'):
                folded(e,.8,.4,3);e['intensity']*=2.5
            for e in pick('Focused cyan edge'):
                folded(e,.85,.3,3);e['intensity']*=2
            for e in pick('Rear offset pupil'):
                e['shift']=[.09,-.03];e['params']['coma']=.25
            for e in pick('Rear blue outer pupil'):
                e['stretch']=[1,1];e['params']['density_bias']=.8
                if not e['label'].endswith(' · rim'):e['intensity']*=.6
            for e in pick('Edge-only'):
                e['intensity']*=3
        elif key=='cooke_anamorphic_sf':
            for e in pick('Source · scatter'):e['intensity']*=.2
            for e in pick('Source · white core'):e['stretch']=[1.8,.7]
            for e in pick('Source · vertical'):e['intensity']*=4
            for e in pick('Flattened blue ghost'):
                e['params'].update(coma=.75,surface_detail=.8,density_bias=.7)
                e['stretch']=[4.1,.8];e['shift'][1]=.025
                e.update(response(e,scale=[[0,1.4],[.45,1.4],[.9,1],[1.7,1.1]],
                                  coma=[[0,0],[.4,.2],[1.3,.75],[2.4,.75]]))
            for e in pick('Blue primary streak'):e['scale']*=.55;e['intensity']*=.3
            for e in pick('Blue reflected streak'):
                e['scale']*=.7;e['intensity']*=.7;e['irregular']=.85
            for e in pick('Layered blue skirt'):
                e['intensity']*=2.5;e['irregular']=.7
            for e in pick('Quiet upright ghost'):
                e['params']['coma']=.65;e['irregular']=.6
        elif key=='cooke_speed_panchro':
            for e in pick('Source · scatter'):
                e['color']=[.72,.88,1];e['intensity']*=.65;e['scale']*=1.2
            for e in pick('Source · fine')+pick('Source · long'):
                e['color']=[.8,.92,1];e['intensity']*=1.3
            for e in pick('Broad cool outer arc'):
                e['offset']=.15;e['scale']=1.65;e['params'].update(thickness=.24,edge_bias=.7,surface_detail=.3)
                e['intensity']*=.9;e['irregular']=.25
                e.update(response(e,scale=[[0,.8],[.45,.8],[1.0,1.1],[1.6,1.9],[2.5,2.4]],
                                  stretch_x=[[0,.9],[.5,.9],[1.3,1.1],[2.6,1.45]],
                                  opacity=[[0,1],[.45,1],[1.0,1],[1.8,.3],[2.6,0]]))
            for e in pick('Single tiny blue'):e['intensity']*=6;e['scale']*=1.3
            for e in pick('Soft veiling'):e['intensity']*=.3
        elif key=='zeiss_super_speed':
            for e in pick('Triangular violet caustic'):
                folded(e,1,.55,3);e['params']['trefoil']=1;e['scale']=.38;e['intensity']*=3;e['rotation']=22
            for e in pick('Far violet pupil'):
                e['params']['hollow']=0;e['params']['density_bias']=.25;e['intensity']*=1.4
            for e in pick('Far violet sliver'):folded(e,.8,.7,3)
        elif key=='panavision_c':
            for e in pick('Opposite lavender oval'):
                e['params'].update(hollow=0,coma=.65,density_bias=.45);e['intensity']*=2
                e['auto_rotate']=True
            for e in pick('Small cyan companion'):folded(e,.8,.5,3);e['intensity']*=2
            for e in pick('Lavender primary streak'):e['intensity']*=3;e['irregular']=.75
            for e in pick('Source · scatter'):e['scale']*=1.6;e['intensity']*=1.5
        elif key=='canon_k35':
            for e in pick('Green focused ghost'):
                folded(e,.8,.55,3);e['intensity']*=4
            for e in pick('Small blue ghost'):e['intensity']*=2
            for e in pick('Violet rear disc'):
                e['params'].update(coma=.55,caustic=.35,blades=3);e['auto_rotate']=True
        elif key=='panavision_primo_classic':
            for e in pick('Cyan focused oval'):folded(e,.85,.6,3);e['intensity']*=2
            for e in pick('Rose secondary cusp'):folded(e,.9,.65,3);e['intensity']*=2
            for e in pick('Bright front pupil')+pick('Front companion pupil'):
                folded(e,.65,.5,3);e['intensity']*=1.4
        elif key=='leitz_hugo':
            for e in pick('Green focused cusp'):
                folded(e,.8,.85,3);e['intensity']*=2
        elif key=='arri_signature':
            for e in pick('Sparse coated ghost'):
                if 'ghost 1' in e['label'] or 'ghost 3' in e['label']:folded(e,.8,.4,3)
            for e in pick('Broken blue far caustic'):folded(e,.9,.65,3)
        elif key=='atlas_mercury':
            for e in pick('Large upper amber pupil'):e.update(vertical_axis(e,-.75));e['params']['coma']=.25
            for e in pick('Small opposite cyan')+pick('Far white'):e['intensity']*=2.5
        elif key=='atlas_orion':
            for e in pick('Offset blue ghost streak'):
                e['scale']*=.45;e['params'].update(curve=.18);e['intensity']*=1.5;e['irregular']=.65
            for e in pick('Lower compressed blue ghost'):e['intensity']*=2;e['params']['coma']=.5
        elif key=='hawk_vlite_vintage74':
            for e in pick('Textured opposite rose oval'):
                e['intensity']*=4;e['params'].update(coma=.35,density_bias=.2,surface_detail=.4)
            for e in pick('Broad blue reflection arc'):
                e['params'].update(edge_bias=.7,surface_detail=.3);e['intensity']*=1.5
            for e in pick('Pale full-width')+pick('Lower secondary'):
                e['irregular']=.8
        elif key=='arri_master_anamorphic':
            for e in pick('Very faint blue ghost streak'):e['scale']*=.7;e['intensity']*=1.8
            for e in pick('Faint upright violet'):e['intensity']*=2;e['params']['coma']=.35
        # A transported fold concentrates a pupil's flux into a small area.
        # It needs a lower incident-energy budget than an untransported disc.
        for e in es:
            if e['params'].get('caustic',0)>0:
                e['intensity']*=.035
        if key=='kowa_cine_prominar':
            for e in pick('Rear disc'):
                if not e['label'].endswith(' · rim'):e['intensity']*=3
                e['params']['density_bias']=.3;e['blur']=.07
                e.update(response(e,stretch_x=[[0,1],[.45,1],[.85,1],[2.4,.85]],
                                  stretch_y=[[0,1.22],[.45,1.22],[.85,1],[2.4,1]]))
            for e in pick('Gold veiling'):e['intensity']*=2
            for e in pick('Blue outer reflection'):
                e['auto_rotate']=True
                e.update(response(e,completion=[[0,360],[.65,360],[1.2,280],[1.7,110],[2.4,50]]))
            for e in es:
                if e['color']==[1,.78,.33]:e['color']=[.9,.9,.48]
        elif key=='zeiss_radiance':
            for e in pick('Source · scatter'):e['params']['scatter']=.04;e['intensity']*=.4
            for e in pick('Source · fine'):
                e['params'].update(ray_taper=0,fan=.035,thickness=.025);e['intensity']*=1.6
            for e in pick('Front blue pupil'):
                e['params'].update(surface_detail=.1,edge_softness=.07)
                e['color']=[.08,.09,1]
                e.update(response(e,scale=[[0,1],[.45,1],[.9,1],[1.4,.55],[1.7,.1],[2.4,.1]]))
            for e in pick('Blue veiling'):e['intensity']*=1.8
            for e in pick('Rear focused pupil'):e['intensity']*=2
        elif key=='cooke_speed_panchro':
            for e in pick('Broad cool outer arc'):
                e.update(response(e,scale=[[0,.85],[.45,.85],[1.0,.95],[1.6,1.5],[2.5,2.4]]))
            for e in pick('Source · scatter'):
                e['intensity']*=.7
        elif key=='cooke_anamorphic_sf':
            for e in pick('Source · scatter'):e['intensity']*=.1
            for e in pick('Flattened blue ghost'):
                e['blur']=.035;e['params']['hollow']=0
                e['intensity']*=.2 if e['label'].endswith(' · rim') else .8


def refine_materials(studies):
    """Hand-reviewed silhouettes and internal density, after the coarse fit.

    Existing Flarecore-authored plates provide local scatter/pupil detail;
    position and shape response remain editable and source-driven. No pixels
    from the commercial reference recordings are embedded in these presets.
    Coarse image loss is not allowed to erase defining concentrated features.
    """
    from flare.motion import MOTION_TARGETS
    for s in studies:
        key=s['key'];es=s['preset']['elements']
        def pick(prefix):return [e for e in es if e['label'].startswith(prefix)]
        def plate(e,ref,gain=1):
            e['type']='texture';e['params']=dict(file=ref,channel='luminance')
            e['slot']=ref.split('/')[0]
            e['intensity']*=gain;e['irregular']=0;e['shade']=0
            e['motion']['channels']=[c for c in e['motion']['channels']
                if 'types' not in MOTION_TARGETS[c['target']] or 'texture' in MOTION_TARGETS[c['target']]['types']]
        def add(e):
            e['id']=f'{key}-material-{len(es)}';es.append(e)
        def pupil(prefix,ref='ghosts/polygon_ghost_02.png',gain=1.5):
            for e in pick(prefix):
                if e['label'].endswith(' · rim'):e['intensity']*=.15
                else:plate(e,ref,gain)
        # The recorded source is an irregular scattering field, not a regular
        # procedural spoke wheel. Keep compact diffraction only for Radiance.
        broad=key in ('panavision_c','cooke_speed_panchro','hawk_vlite_vintage74')
        for e in pick('Source ·'):
            if e['label']=='Source · white core':
                e['scale']=.15 if broad else .10;e['intensity']=.15;e['color']=[1,1,1]
                if key=='zeiss_radiance':e['intensity']=3
                e['params']=dict(softness=.38,falloff=2.1)
            elif e['label']=='Source · fine diffraction' and key!='zeiss_radiance':
                plate(e,'glows/study_photographic_scatter.png');e['slot']='glows'
                e['scale']=1.7 if broad else 1.0;e['stretch']=[1,1]
                e['intensity']=.85 if broad else .7;e['color']=[.85,.95,1];e['blur']=.015
            elif e['label']=='Source · scatter halo':
                e['intensity']*=.35;e['params']['scatter']=0
            elif e['label']=='Source · long rays':e['intensity']*=.1
        if key=='kowa_cine_prominar':
            pupil('Rear disc',gain=2.5)
            pupil('Front gold oval','ghosts/smeared_disc.png',2)
            pupil('Near rimmed ghost','ghosts/smeared_disc.png',2)
            for e in pick('Rear disc'):
                e['auto_rotate']=False;e['stretch']=[1,1];e['scale']*=.9
                e.update(response(e,stretch_x=[[0,1],[2.4,1]],stretch_y=[[0,1],[2.4,1]]))
                if e['type']=='texture':
                    e['params']['file']='ghosts/study_nested_pupil.png';e['scale']*=1.3;e['intensity']*=1.5
                if e['label'].startswith('Rear disc A'):e['offset']=1.85
                if e['label'].startswith('Rear disc C'):e['offset']=2.22
                e.update(response(e,opacity=[[0,4],[.45,4],[.9,1.6],[1.8,.7],[2.5,0]]))
                if e['type']=='texture':
                    fill=copy.deepcopy(e);fill['label']+=' · scattered fill';fill['type']='iris';fill['scale']*=.75
                    fill['params']=dict(blades=9,roundness=1,edge_softness=.2,density_bias=-.3,surface_detail=.6)
                    fill['intensity']=.02;fill['color']=[.75,.75,.45];fill['shade']=.2
                    add(fill)
            for e in pick('Front gold oval'):
                e['scale']*=1.0;e['stretch']=[.8,1];e['offset']=-.95
            for e in pick('Blue outer reflection'):
                e['auto_rotate']=False;e['stretch']=[1,1];e['offset']=1
                e.update(response(e,completion=[[0,360],[2.4,360]],stretch_x=[[0,.73],[.9,.86],[1.8,1.05],[2.5,1.4]]))
            for e in pick('Gold veiling'):e['intensity']*=.22
            for e in pick('Focused cyan caustic'):
                plate(e,'ghosts/study_nested_pupil.png');e['scale']=.33;e['stretch']=[1,1]
                e['intensity']=.65;e['color']=[.35,.75,1]
            for e in pick('Focused cyan knot'):
                e['intensity']*=3;e['color']=[.35,.75,1]
            for e in pick('Uneven microghost scatter'):e['intensity']*=3
        elif key=='zeiss_radiance':
            pupil('Rear blue outer pupil',gain=2)
            pupil('Rear offset pupil',gain=1.5)
            for e in pick('Rear blue outer pupil')+pick('Rear offset pupil'):
                e['stretch']=[1,1];e['scale']*=.95
                e.update(response(e,scale=[[0,1],[.5,1],[.9,1],[1.7,1.45],[2.4,1.7]]))
                if e['type']=='texture':
                    e['type']='iris';e['params']=dict(blades=9,roundness=1,edge_softness=.14,density_bias=.55,surface_detail=.5)
                    e['intensity']*=.65;e['blur']=.04
            for e in pick('Front blue pupil'):
                e['stretch']=[1,1];e['scale']*=.95;e['offset']=-.46
            for e in pick('Rear focused pupil'):
                if e['label'].endswith(' · rim'):e['intensity']=0
                else:
                    plate(e,'ghosts/study_nested_pupil.png');e['scale']=.55;e['stretch']=[1,1]
                    e['color']=[.25,.55,1];e['intensity']=.5;e['blur']=.015
            for e in pick('Focused blue pupil knot'):
                e['intensity']=.18 if e['type']=='glow' else .02;e['color']=[.3,.6,1]
        elif key=='cooke_speed_panchro':
            for e in pick('Single tiny blue reflection'):
                e['color']=[.2,.6,1];e['intensity']=2 if not e['label'].endswith(' · rim') else .1
                e['blur']=.025;e['intensity']*=.55
                e.update(response(e,scale=[[0,2],[.5,2],[1,1],[2.4,1]]))
            for e in pick('Broad cool outer arc'):
                e['params']['surface_detail']=.3;e['params']['edge_bias']=.2
                e['offset']=-.1;e['stretch']=[1,1]
                e.update(response(e,scale=[[0,.8],[.45,.8],[1,.95],[1.6,1.5],[2.5,2.4]]))
        elif key=='zeiss_super_speed':
            pupil('Far violet pupil',gain=2)
            for e in pick('Triangular violet caustic'):
                e['rotation']+=20;e['params']['coma']=.85
                e.update(response(e,coma=[[0,0],[.45,0],[.85,.85],[2.4,.85]],
                                  opacity=[[0,3],[.5,3],[.9,1],[1.8,.8],[2.5,0]]))
            add(response(element('glow','Central blue-white pupil focus',2,.13,.3,[.3,.5,1],params=dict(softness=.4,falloff=2)),
                         opacity=[[0,1],[.45,1],[.8,.1],[1.2,0]]))
            add(response(element('glow','Central small green reflection',2.5,.025,.7,[.12,1,.6],params=dict(softness=.5,falloff=2)),
                         opacity=[[0,1],[.45,1],[.8,0],[2.4,0]]))
            for e in pick('Blue violet veil'):e['intensity']*=.65
        elif key=='cooke_anamorphic_sf':
            pupil('Flattened blue ghost','ghosts/smeared_disc.png',2)
            for e in pick('Layered blue skirt'):e['intensity']*=1.5;e['scale']*=.32
            for e in pick('Blue reflected streak'):e['scale']*=.4;e['intensity']*=.7
            for e in pick('Source · fine'):e['intensity']*=.08;e['stretch']=[1.3,.65]
            for e in pick('Source · vertical'):e['intensity']*=.04
            for e in pick('Quiet upright ghost'):
                if not e['label'].endswith(' · rim'):
                    plate(e,'ghosts/smeared_disc.png',4);e['stretch'][1]*=1.4
                    e['intensity']*=.45
            for e in pick('Blue primary streak'):e['scale']*=.65
        elif key=='panavision_primo_classic':
            pupil('Large green pupil',gain=1.3)
            for e in pick('Large green pupil'):e['scale']*=.65
            for e in pick('Cyan focused oval'):
                if not e['label'].endswith(' · rim'):
                    plate(e,'ghosts/study_nested_pupil.png');e['scale']=.3;e['stretch']=[.48,1.25]
                    e['intensity']=.5;e['color']=[.04,.66,.8]
            for e in pick('Rose secondary cusp'):e['intensity']*=4
        elif key=='leitz_hugo':
            for e in pick('Green focused cusp'):
                e['intensity']=.04 if not e['label'].endswith(' · rim') else .005
                e['color']=[.08,1,.32]
                e.update(response(e,opacity=[[0,4],[.5,4],[1.2,2],[1.9,.08],[2.5,0]]))
                e['intensity']*=.3;e['blur']=.018
            for e in pick('Faint enclosing blue pupil'):
                e['params']['surface_detail']=.65;e['intensity']*=.5
        elif key=='canon_k35':
            pupil('Front rose pupil','ghosts/smeared_disc.png',1.5)
            pupil('Near soft pupil','ghosts/polygon_ghost_02.png',1.2)
            pupil('Violet rear disc',gain=2)
            for e in pick('Front rose pupil')+pick('Near soft pupil'):
                e['stretch']=[1,1];e['scale']*=.8
            for e in pick('Green focal knot'):
                e['color']=[.2,1,.55];e['intensity']*=5
            for e in pick('Green focused ghost'):
                if not e['label'].endswith(' · rim'):
                    plate(e,'caustics/caustic_fold_03.png');e['intensity']=.4;e['color']=[.12,1,.4]
                    e['scale']=.15
        elif key=='panavision_c':
            pupil('Opposite lavender oval',gain=2)
            for e in pick('Opposite lavender oval'):
                e['auto_rotate']=False;e['stretch']=[1.3,.65];e['rotation']=-12
            for e in pick('Small cyan focused edge'):e['intensity']*=5;e['color']=[.18,.6,1]
        elif key=='hawk_vlite_vintage74':
            pupil('Textured opposite rose oval','ghosts/smeared_disc.png',1.5)
        elif key=='atlas_mercury':
            pupil('Large upper amber pupil','ghosts/smeared_disc.png',2)
            pupil('Lower rose reflection','ghosts/smeared_disc.png',1.5)
        elif key=='arri_signature':
            for e in pick('Blue focused reflection')+pick('Far blue folded reflection'):
                e['intensity']*=2


def build(apply_fits=True,apply_materials=True):
    studies=[]
    gold=[1,.78,.33]; blue=[.055,.25,1]; cyan=[.22,.62,1]
    e=source(.55,.35)
    for label,t,s,g,stretch in [('Front gold oval',-.92,.22,.11,(.65,1)),
                               ('Near gold sliver',-.28,.085,.24,(.5,1)),
                               ('Near rimmed ghost',.42,.12,.13,(1,1)),
                               ('Rear disc A',1.78,.30,.027,(1,1)),
                               ('Rear disc B',2.03,.25,.055,(1,1)),
                               ('Rear disc C',2.40,.46,.025,(1,1))]:
        for d in disc(label,t,s,g,gold,stretch,rim=1.3):
            far_shift=max(t-1.3,0)*-.47
            e.append(response(d,scale=[[0,1.1],[.4,1.3],[.85,1],[1.7,1.0],[2.8,.7]],
                              offset=[[0,0],[.9,0],[1.8,far_shift],[2.8,far_shift]],
                              stretch_x=[[0,1.35],[.4,1.35],[.85,1],[2.4,.85]]))
    for j,(t,s,g) in enumerate([(.73,.025,.14),(.93,.017,.08),(1.1,.031,.09),(1.27,.043,.065),(1.4,.021,.12)]):
        e+=disc(f'Uneven microghost {j+1}',t,s,g,gold,rim=.4)
    e += [response(ring('Blue outer reflection',1.05,2.12,.065,cyan,.12,irregular=.15),
                   opacity=[[0,.35],[.45,1.1],[.85,1],[1.5,.55],[2.4,.02],[3,0]],
                   stretch_x=[[0,.72],[.8,.86],[1.8,1.18],[2.5,1.4]]),
          ring('Focused cyan caustic',2.04,.10,.17,cyan,.05,stretch=[1.5,.8],irregular=.35),
          element('glow','Gold veiling reflection',1,2.2,.025,[.8,.83,.6],params=dict(softness=.8,falloff=2))]
    studies.append(study('kowa_cine_prominar','Kowa Cine Prominar','Spherical',25,4,'pair01','left',e,
        'Gold front slivers, uneven microghost chain, overlapping rimmed rear discs and a blue outer reflection. T2.3 comparison kept separate; no aperture interpolation claimed.'))

    e=source(.25,.23)
    e += [line('Blue primary streak',0,.68,.045,blue,.009),
          vertical_axis(line('Blue reflected streak',.6,.72,.19,blue,.025),0)]
    for j,(t,s) in enumerate([(.28,.055),(.49,.072),(.66,.048)]):
        e += [vertical_axis(d,0) for d in disc(f'Flattened blue ghost {j+1}',t,s,.20,blue,(2.4,.43),rim=.25)]
    for j,(t,s) in enumerate([(1.35,.09),(1.56,.14)]):
        for d in disc(f'Quiet upright ghost {j+1}',t,s,.008,cyan,(.55,1.6),rim=.15):
            e.append(response(d,opacity=[[0,.45],[.45,1.1],[.9,.55],[1.6,.1],[2.5,0]]))
    studies.append(study('cooke_anamorphic_sf','Cooke Anamorphic SF','Anamorphic',32,2.8,'pair01','right',e,
        'Blue narrow streak plus flattened reflected lobes at source height; faint upright ghosts below. Not a generic warm oval chain.'))

    e=source(.20,.18)
    for j,(t,s,g,c) in enumerate([(1.65,.08,.025,blue),(2.02,.033,.04,[1,.63,.4]),(2.32,.15,.006,blue),(.42,.014,.04,cyan)]):
        e+=disc(f'Sparse coated ghost {j+1}',t,s,g,c,rim=.2)
    e += [response(ring('Broken blue far caustic',2.3,.17,.018,blue,.08,stretch=[1,.65],irregular=.4),
                   opacity=[[0,0],[.5,.2],[1.1,1],[1.8,.6],[2.5,0]])]
    studies.append(study('arri_signature','ARRI Signature Prime','Spherical',35,2.8,'pair03','left',e,
        'Quiet cool-white source, sparse blue ghosts and one small warm point; no large warm halo.'))

    e=source(.30,.28)
    for label,t,s,g,stretch in [('Front blue pupil',-.4,.98,.037,(.83,1.15)),
                              ('Rear blue outer pupil',2.08,.56,.043,(.83,1)),
                              ('Rear offset pupil',2.22,.48,.018,(.91,1)),
                              ('Rear focused pupil',1.98,.19,.075,(.9,1.1))]:
        for d in disc(label,t,s,g,blue,stretch,rim=1.4):
            e.append(response(d,scale=[[0,1.3],[.4,1.3],[.9,1],[1.7,1.45],[2.4,1.7]],
                              crescent=[[0,0],[1.7,0],[2.4,.4]]))
    for label,t,s in [('Edge-only middle pupil',.65,.42),('Edge-only near pupil',.17,.22)]:
        for d in disc(label,t,s,.025,blue,rim=.3):
            e.append(response(d,opacity=[[0,0],[.9,0],[1.7,1],[2.5,.35],[3.6,0]]))
    e += [element('glow','Blue veiling field',1,3.4,.05,blue,params=dict(softness=.9,falloff=2)),
          ring('Focused cyan edge',1.98,.15,.04,cyan,.07,irregular=.23)]
    studies.append(study('zeiss_radiance','ZEISS Supreme Radiance','Spherical',35,2.8,'pair03','right',e,
        'Large blue pupil before source, clustered blue opposite pupils and substantial blue veil. Shape clipping increases off-axis.'))

    e=source(.22,.30)
    e += [line('Fine blue primary streak',0,3.8,.20,blue,.0025),
          vertical_axis(line('Offset blue ghost streak',1.4,1.9,.045,blue,.018),2),
          vertical_axis(line('Short cyan line segment',1,.9,.055,cyan,.003),0)]
    e+=disc('Lower compressed blue ghost',1.6,.13,.017,blue,(3,.35),rim=.1)
    studies.append(study('atlas_orion','Atlas Orion','Anamorphic',50,2.8,'pair05','left',e,
        'Fine long blue line and a separate weaker horizontal reflection below the source. No large amber ghost chain.'))

    e=source(.65,.45,(.7,.93,1),1.7)
    e += [line('Lavender primary streak',0,3.5,.08,[.75,.58,1],.006),
          element('glow','Cool veiling scatter',.5,3.2,.045,[.4,.62,1],params=dict(softness=.8,falloff=1.3),irregular=.35)]
    for d in disc('Opposite lavender oval',2.0,.24,.033,[.28,.23,1],(1.3,.58),rim=1.2):
        e.append(response(d,stretch_y=[[0,.85],[.8,1],[1.6,.6],[2.4,.4]]))
    e+=disc('Small cyan companion',1.86,.105,.04,cyan,(1.3,.7),rim=.6)
    e+=disc('Front rose glint',-.34,.045,.055,[1,.3,.52],(1.7,.5),rim=.4)
    studies.append(study('panavision_c','Panavision C Series','Anamorphic',50,2.8,'pair05','right',e,
        'Broad cool scatter, pale lavender line and a compact tilted opposite purple/cyan oval, unlike Orion.'))

    e=source(.46,.34)
    for label,t,s,g,c in [('Front rose pupil',-.12,.65,.022,[1,.24,.4]),
                         ('Near soft pupil',.18,.41,.025,[.7,.7,1]),
                         ('Small blue ghost',1.47,.12,.025,blue),
                         ('Green focused ghost',1.84,.1,.08,[.12,1,.4]),
                         ('Violet rear disc',1.98,.3,.019,[.42,.15,1])]:
        e+=disc(label,t,s,g,c,rim=.65)
    e += [ring('Far prismatic arc',2.45,.4,.08,[.15,.6,1],.04,dispersion=2,dispersion_samples=7),
          element('glow','Cool-warm veiling reflection',1,3,.018,[.45,.5,1],params=dict(softness=.8,falloff=1.6))]
    studies.append(study('canon_k35','Canon K35','Spherical',50,2.8,'pair07','left',e,
        'Rose front pupil, a blue/green/violet opposite cluster and clipped spectral far edge. Not uniformly amber.'))

    e=source(.65,.38,(.78,1,.88),1.8)
    e += [response(ring('Broad cool outer arc',.65,2.45,.055,[.4,.7,1],.15,irregular=.22),
                   stretch_x=[[0,.78],[.9,1],[1.8,1.2],[2.6,1.45]]),
          element('glow','Soft veiling skirt',.2,2.8,.026,[.4,.66,.7],params=dict(softness=.8,falloff=1.8))]
    e+=disc('Single tiny blue reflection',2.05,.022,.06,cyan,rim=.1)
    studies.append(study('cooke_speed_panchro','Cooke Speed Panchro','Spherical',50,2.8,'pair07','right',e,
        'Large gentle neutral/cool source, broad outer arc, almost no large ghost chain. This is not Panchro-i Classic FF.'))

    e=source(.18,.23)
    for d in disc('Green focused cusp',2.02,.23,.033,[.08,1,.32],(.2,1),rim=1.8):
        d=response(d,stretch_x=[[0,5],[.42,5],[1.1,1],[2.4,.4]],
                     opacity=[[0,1],[.5,1],[1.2,1],[1.9,.08],[2.5,0]])
        d['motion']['channels'].append(curve('rotation',[[-1,25],[0,0],[1,-25]],'x'))
        e.append(d)
    e += [ring('Faint enclosing blue pupil',1.98,.48,.005,[.04,.3,.7],.045),
          response(ring('Small front amber crescent',-.32,.12,.035,[.7,.66,.2],.055),
                   crescent=[[0,.6],[1,.8],[2,.94]],opacity=[[0,0],[.6,.3],[1.1,1],[1.8,.4],[2.7,0]])]
    studies.append(study('leitz_hugo','Leitz HUGO','Spherical',35,2.8,'pair09','left',e,
        'Sparse green cusp inside a very faint blue pupil; small front amber arc, otherwise deep black.'))

    e=source(.16,.19)
    e += [ring('Very faint blue outer arc',1,2.2,.003,cyan,.15),
          response(ring('Near-invisible opposite pupil',1.9,.34,.0015,[.13,.5,.43],.13),
                   stretch_x=[[0,1],[1,.55],[2,.3]],crescent=[[0,0],[1,.35],[2.4,.8]])]
    e+=disc('Tiny pale ghost',.7,.018,.006,cyan,rim=.1)
    studies.append(study('arri_master_prime','ARRI Master Prime','Spherical',35,2.8,'pair09','right',e,
        'Very restrained white/cool source, nearly invisible green/blue reflections. Quiet is intentional.'))

    e=source(.24,.24)
    e += [line('Fine gold primary streak',0,3.5,.105,[1,.48,.1],.004),
          vertical_axis(line('Dim lower reflected streak',1.7,1.8,.006,cyan,.016),2),
          element('glow','Blue source scatter',0,1.2,.055,cyan,params=dict(softness=.6,falloff=2))]
    e += [vertical_axis(d,-1.8) for d in disc('Large upper amber pupil',1.52,.56,.014,[1,.48,.19],(1.1,.95),rim=.25)]
    e+=disc('Lower rose reflection',1.88,.27,.007,[.64,.3,.5],(2.2,.65),rim=.1)
    e+=disc('Small opposite cyan point',1.48,.024,.045,cyan,rim=.25)
    e+=disc('Far white point',2.12,.014,.10,[1,1,1],rim=.1)
    studies.append(study('atlas_mercury','Atlas Mercury','Anamorphic',54,2.8,'pair11','left',e,
        'Fine gold line, large clipped upper amber pupil and quiet lower blue/rose reflections.'))

    e=source(.8,.3,(.75,.86,1),1.45)
    e += [line('Pale full-width streak',0,4,.48,[.7,.82,1],.01),
          vertical_axis(line('Lower secondary streak',1.55,2.7,.14,[.6,.69,1],.007),2),
          vertical_axis(element('glow','Opposite streak hotspot',1.8,.19,.8,[.65,.8,1],stretch=[2,.55],params=dict(softness=.6,falloff=1.8)),0),
          element('glow','Blue veiling field',.8,4,.16,[.28,.42,.85],params=dict(softness=1,falloff=1.3)),
          ring('Broad blue reflection arc',1.1,1.68,.035,cyan,.15,stretch=[1.6,.7],irregular=.25)]
    e+=disc('Textured opposite rose oval',1.9,.56,.024,[.9,.56,.65],(1.5,.56),rim=.15)
    studies.append(study('hawk_vlite_vintage74','Hawk V-Lite Vintage 74','Anamorphic',55,2.8,'pair11','right',e,
        'Strong pale double streak, broad blue veil and an opposite rose textured oval. The site does not establish a 1.3x variant.'))

    e=source(.28,.29)
    e+=disc('Bright front pupil',-.16,.095,.45,[.7,.88,.9],(1.45,.65),rim=.7)
    e+=disc('Front companion pupil',-.3,.082,.22,[.65,.88,.88],(1.4,.65),rim=.5)
    e+=disc('Large green pupil',1.76,.43,.014,[.13,.65,.2],rim=.15)
    e+=disc('Cyan focused oval',1.83,.23,.06,[.04,.66,.8],(.48,1.25),rim=.6)
    e += [ring('Rose secondary cusp',1.66,.065,.095,[1,.18,.48],.15,stretch=[.55,1.3]),
          ring('Clipped cool outer reflection',1.4,2.0,.04,cyan,.1,irregular=.3)]
    studies.append(study('panavision_primo_classic','Panavision Primo Classic','Spherical',50,2.8,'pair13','left',e,
        'Bright front white pupils, opposite cyan/green oval and pink cusp, plus a broad clipped edge reflection.'))

    e=source(.2,.18)
    e += [vertical_axis(line('Very faint blue ghost streak',1.9,.7,.011,[.16,.15,1],.016),2),
          element('glow','Restrained blue envelope',.3,1.1,.024,[.18,.37,.9],params=dict(softness=.7,falloff=2))]
    e+=disc('Faint upright violet pupil',2.03,.15,.004,[.35,.17,1],(.4,1.2),rim=.1)
    studies.append(study('arri_master_anamorphic','ARRI Master Anamorphic','Anamorphic',50,2.8,'pair13','right',e,
        'Quiet source with a weak displaced blue streak; this is the standard lens test, not an added Flare Set.'))

    e=source(.38,.22)
    e+=disc('Far violet pupil',1.89,.44,.025,[.27,.14,1],rim=.6)
    e += [element('iris','Triangular violet caustic',1.93,.13,.10,[.55,.35,1],params=dict(blades=3,roundness=.08,edge_softness=.45,surface_detail=.25),rotation=13),
          ring('Soft amber pupil rim',1.91,.46,.018,[.65,.43,.15],.1),
          ring('Far violet sliver',2.18,.12,.055,[.5,.14,1],.11,stretch=[.35,1.1]),
          ring('Blue outer reflection',1,2.22,.026,cyan,.12,irregular=.18),
          element('glow','Blue violet veil',.8,3,.024,[.24,.27,.72],params=dict(softness=.8,falloff=1.6))]
    e+=disc('Clipped front white pupil',-.62,.32,.09,[.73,.84,.75],(.5,1.3),rim=.15)
    studies.append(study('zeiss_super_speed','ZEISS Super Speed','Spherical',50,2.8,'pair15','left',e,
        'Triangular violet caustic inside a soft violet/amber pupil, far sliver and a clipped front white ghost. Standard coated test, not the uncoated variant.'))

    refine_anatomy(studies)
    refine_transport(studies)
    for s in studies:
        s['preset'].pop('global_')
        s['preset']['global'] = dict(seed=2709,edge_fade_start=.25,edge_fade_range=.65)
    if apply_fits:
        path=Path(__file__).resolve().parents[1]/'docs/reference_study_fits.json'
        if path.exists():
            fit_data=json.loads(path.read_text(encoding='utf-8'))
            fitted=fit_data['colors']
            for s in studies:
                for e in s['preset']['elements']:
                    if e['id'] in fit_data.get('geometry',{}):
                        e.update(copy.deepcopy(fit_data['geometry'][e['id']]))
                    if e['id'] in fitted and (fit_data.get('fit_source') or not e['label'].startswith('Source ·')):
                        e['color']=fitted[e['id']]
    if apply_materials:refine_materials(studies)
    return studies


if __name__ == '__main__':
    print(json.dumps(build(),ensure_ascii=True,separators=(',',':')))
