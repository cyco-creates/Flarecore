# SPDX-License-Identifier: Apache-2.0
"""Shipped workflows must stay aligned with the nodes they drive.

widgets_values is a positional array. If a node gains, loses or reorders a
widget — or a front-end extension reorders node.widgets — every value after
the change lands in the wrong input, and ComfyUI reports it as a type error
far from the cause ("could not convert string to float: 'manual'"). These
tests type-check each shipped workflow against the live INPUT_TYPES.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_nodes import PKG  # noqa: E402  (loads the package the way ComfyUI does)

WORKFLOW_DIR = ROOT / "example_workflows"
CONNECTION_TYPES = {"IMAGE", "MASK", "LATENT", "MODEL", "CLIP", "VAE",
                    "CONDITIONING", "STRING_LIST"}


def workflows():
    return sorted(WORKFLOW_DIR.glob("*.json"))


def declared_widgets(node_cls):
    """Ordered (name, spec) for inputs that render as widgets."""
    types = node_cls.INPUT_TYPES()
    order = types.get("input_order", {})
    names = list(order.get("required", list(types.get("required", {})))) + \
            list(order.get("optional", list(types.get("optional", {}))))
    merged = {**types.get("required", {}), **types.get("optional", {})}
    out = []
    for name in names:
        spec = merged.get(name)
        if spec is None:
            continue
        head = spec[0]
        if isinstance(head, list):          # COMBO
            out.append((name, spec))
        elif head not in CONNECTION_TYPES:  # INT / FLOAT / STRING / BOOLEAN
            out.append((name, spec))
    return out


def check_value(name, spec, value):
    head = spec[0]
    if isinstance(head, list):
        assert value in head, (
            f"{name}: {value!r} is not one of {head} — widget values look shifted")
    elif head == "INT":
        assert isinstance(value, int) and not isinstance(value, bool), \
            f"{name}: expected an int, got {value!r} — widget values look shifted"
    elif head == "FLOAT":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), \
            f"{name}: expected a number, got {value!r} — widget values look shifted"
    elif head == "BOOLEAN":
        assert isinstance(value, bool), \
            f"{name}: expected a bool, got {value!r} — widget values look shifted"
    elif head == "STRING":
        assert isinstance(value, str), \
            f"{name}: expected a string, got {value!r} — widget values look shifted"


@pytest.mark.parametrize("path", workflows(), ids=lambda p: p.stem)
def test_workflow_widget_values_align(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    checked = 0
    for node in data["nodes"]:
        cls = PKG.NODE_CLASS_MAPPINGS.get(node["type"])
        if cls is None:
            continue  # third-party or core node
        # A widget promoted to a connection keeps its slot in widgets_values
        # (verified against a live graph), so every declared widget counts.
        expected = declared_widgets(cls)
        values = node.get("widgets_values", [])
        # values may run SHORT of the declared widgets: a workflow saved
        # before a node gained a trailing input loads with the new widget at
        # its default, and that forward-compatibility is deliberate. A SHIFT,
        # by contrast, puts a wrong-typed value somewhere in the prefix and
        # the type checks below catch it. The frontend appends a
        # control_after_generate string after a seed widget; consume it.
        vi = 0
        for name, spec in expected:
            if vi >= len(values):
                break
            check_value(name, spec, values[vi])
            checked += 1
            vi += 1
            if name in ("seed", "noise_seed") and vi < len(values) and \
                    values[vi] in ("fixed", "randomize", "increment", "decrement"):
                vi += 1
    assert checked > 0, f"{path.name} exercised no flarecore widgets"


@pytest.mark.parametrize("path", workflows(), ids=lambda p: p.stem)
def test_workflow_presets_are_valid(path):
    """Any preset_json baked into a workflow must actually validate."""
    from flare.schema import load_preset
    data = json.loads(path.read_text(encoding="utf-8"))
    for node in data["nodes"]:
        if node["type"] != "FlareRender":
            continue
        # a connected preset_json is supplied at run time, not baked in
        if any(i["name"] == "preset_json" for i in node.get("inputs", [])):
            continue
        names = [n for n, _ in declared_widgets(PKG.NODE_CLASS_MAPPINGS["FlareRender"])]
        text = node["widgets_values"][names.index("preset_json")]
        load_preset(text)  # raises if the slot holds something else


def test_workflows_exist_and_are_registered():
    found = {p.stem for p in workflows()}
    # the studio hosts all three benches behind its switch; the trigger lab
    # stays separate as the no-assets rule-animation demo
    assert {"flarecore_studio", "flarecore_trigger_lab"} <= found
    # the directory name ComfyUI scans for custom-node templates
    assert WORKFLOW_DIR.name == "example_workflows"


def test_studio_depth_is_connected_in_both_labs():
    doc = json.loads((WORKFLOW_DIR / "flarecore_studio.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in doc["nodes"]}
    links = {link[0]: link for link in doc["links"]}
    assert len(nodes) == len(doc["nodes"])
    assert len(links) == len(doc["links"])
    for name in ("FLARE LAB", "VIDEO LAB"):
        group = next(g for g in doc["groups"] if g["title"] == name)
        x, y, w, h = group["bounding"]
        inside = lambda n: x <= n["pos"][0] < x+w and y <= n["pos"][1] < y+h
        render = next(n for n in nodes.values() if n["type"] == "FlareRender" and inside(n))
        depth_input = next(i for i in render["inputs"] if i["name"] == "depth")
        depth = nodes[links[depth_input["link"]][1]]
        assert depth["type"] == "DepthAnythingV2Preprocessor"
        assert inside(depth) and depth["mode"] == render["mode"]
        image_input = next(i for i in render["inputs"] if i["name"] == "image")
        assert links[depth["inputs"][0]["link"]][1:3] == links[image_input["link"]][1:3]
    for link_id, source, slot, target, target_slot, kind in doc["links"]:
        assert link_id in nodes[source]["outputs"][slot]["links"]
        assert nodes[target]["inputs"][target_slot]["link"] == link_id


def test_studio_notes_and_forge_layout():
    doc = json.loads((WORKFLOW_DIR / "flarecore_studio.json").read_text(encoding="utf-8"))
    for node in doc["nodes"]:
        if node["type"] == "Note":
            assert node["widgets_values_named"]["text"] == node["widgets_values"][0]
            assert node.get("title")
    forge_ids = {1, 10, 11, 12, 13, 30, 34, 35}
    nodes = [n for n in doc["nodes"] if n["id"] in forge_ids]
    gx, gy, gw, gh = next(g["bounding"] for g in doc["groups"] if g["title"] == "ELEMENT FORGE")
    for i, a in enumerate(nodes):
        x, y = a["pos"]; w, h = a["size"]
        assert gx <= x and gy <= y and x+w <= gx+gw and y+h <= gy+gh
        for b in nodes[i+1:]:
            bx, by = b["pos"]; bw, bh = b["size"]
            assert x+w <= bx or bx+bw <= x or y+h <= by or by+bh <= y


@pytest.mark.parametrize("path", workflows(), ids=lambda p: p.name)
def test_subgraph_definitions_ship_live_nodes(path):
    """A subgraph definition freezes the mode its nodes had when it was made.

    Converting a selection into a subgraph copies each node's current mode
    into the definition, and nothing ever clears it again: muting is a
    property of the subgraph INSTANCE in the parent graph, so the studio
    switch (which mutes the instance) cannot reach inside. Ship a definition
    built from muted or bypassed nodes and the bench is dead on arrival --
    the generator contributes nothing and the node downstream of it fails
    with "Required input is missing".
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    for sub in (doc.get("definitions") or {}).get("subgraphs", []):
        for node in sub.get("nodes", []):
            assert node.get("mode", 0) == 0, (
                f"{path.name}: subgraph {sub.get('name')!r} node {node.get('id')} "
                f"({node.get('type')}) ships at mode {node.get('mode')} — "
                "a definition must contain live nodes; mute the instance instead")
