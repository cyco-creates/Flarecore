# SPDX-License-Identifier: Apache-2.0
"""Data, organization and render safety for the independent lens studies."""
import json
import warnings
from pathlib import Path
import pytest
import torch
from flare.schema import validate_preset, ELEMENT_SLOTS
from flare.engine import render_stack
from flare.grid import uv_to_grid

ROOT=Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'docs/lens_inspired_sources.json').read_text(encoding='utf-8'))
FILES=sorted(ROOT/record['file'] for record in CATALOG['presets'])


def test_fifteen_named_studies_are_organized_and_attributed():
    assert len(FILES)==len(CATALOG['presets'])==15
    assert {p.relative_to(ROOT).as_posix() for p in FILES}=={r['file'] for r in CATALOG['presets']}
    assert len({r['name'] for r in CATALOG['presets']})==15
    assert all(r['source'].startswith('https://') and r['basis'] for r in CATALOG['presets'])
    assert len({r['subcategory'] for r in CATALOG['presets']})==3
    assert sum(r['category']=='Anamorphic' for r in CATALOG['presets'])==6


@pytest.mark.parametrize('path',FILES,ids=lambda p:p.stem)
def test_study_validates_and_renders_odd_portrait_repeatably(path):
    raw=json.loads(path.read_text(encoding='utf-8'))
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        preset=validate_preset(raw)
    assert raw['name'].endswith(' · inspired')
    assert raw['subcategory'].startswith('Lens inspired · ')
    assert not preset.get('lens_lab',{}).get('enabled')
    assert len({e['id'] for e in preset['elements']})==len(preset['elements'])
    assert all(e['slot'] in ELEMENT_SLOTS for e in preset['elements'])
    # Self-contained procedural looks; no hidden paid generation/texture input.
    assert all(e['type']!='texture' for e in preset['elements'])
    h,w=97,65
    x,y=uv_to_grid(.73,.29,h,w)
    lights=[dict(x=x,y=y,brightness=1.)]
    a=render_stack(preset,lights,h,w,'cpu',torch.float32)
    b=render_stack(preset,lights,h,w,'cpu',torch.float32)
    assert a.shape==(h,w,3) and torch.isfinite(a).all() and a.min()>=0 and a.max()>0
    assert torch.equal(a,b)


def test_studies_are_not_identical_stacks_with_new_names():
    signatures=[]
    for path in FILES:
        raw=json.loads(path.read_text(encoding='utf-8'))
        geometry=[(e['type'],e.get('offset',0),e['scale'],e.get('stretch'),e.get('params')) for e in raw['elements']]
        signatures.append(json.dumps(geometry,sort_keys=True))
    assert len(set(signatures))==15
