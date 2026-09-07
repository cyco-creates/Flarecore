# SPDX-License-Identifier: Apache-2.0
import io
import json
import pytest
import torch
from PIL import Image
from test_nodes import PKG
from flare.schema import load_preset
from flare.engine import render_stack


def preset_json(data):
    return json.dumps({'schema_version': 1, **data})


def test_master_default_preserves_existing_presets():
    p=load_preset(preset_json({'elements':[{'type':'glow'}]}))
    assert p['global']['master']==1


def test_master_links_energy_and_scale_without_overwriting_base_values():
    p=load_preset(preset_json({'global':{'intensity':.7,'scale':.8,'master':.5},'elements':[{'type':'glow','scale':.2}]}))
    lights=[dict(x=0.,y=0.,brightness=1.)]
    actual=render_stack(p,lights,48,64,'cpu',torch.float32)
    p['global'].update(master=1.,intensity=.35,scale=.4)
    expected=render_stack(p,lights,48,64,'cpu',torch.float32)
    assert torch.allclose(actual,expected)
    p['global']['master']=0
    assert render_stack(p,lights,48,64,'cpu',torch.float32).count_nonzero()==0


@pytest.mark.parametrize('value',[-1,float('nan'),float('inf')])
def test_invalid_master_rejected(value):
    with pytest.raises(ValueError):load_preset(preset_json({'global':{'master':value},'elements':[]}))


def test_complete_preset_preview_is_a_real_png():
    png=PKG.nodes.api._render_preset_png(preset_json({'elements':[{'type':'glow'},{'type':'iris','offset':1.}]}))
    im=Image.open(io.BytesIO(png))
    assert im.size==(512,288)
    assert im.getbbox() is not None


def test_texture_preset_preview_resolves_the_library():
    files=PKG.nodes.library.list_elements()
    ref=files[0] if isinstance(files[0],str) else files[0]['ref']
    png=PKG.nodes.api._render_preset_png(preset_json({'elements':[{'type':'texture','params':{'file':ref}}]}))
    assert Image.open(io.BytesIO(png)).getbbox() is not None


def test_preview_names_cannot_escape_preset_directory():
    for name in ('../LICENSE','..\\LICENSE','/LICENSE'):
        assert PKG.nodes.api._find_preset(name) is None
