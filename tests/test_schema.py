# SPDX-License-Identifier: Apache-2.0
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.schema import (
    validate_preset,
    load_preset,
    ELEMENT_TYPES,
    ELEMENT_COMMON_DEFAULTS,
    PARAM_DEFAULTS,
)


def test_minimal_preset_fills_all_defaults():
    preset = validate_preset({"schema_version": 1, "elements": [{"type": "glow"}]})
    elem = preset["elements"][0]
    for key in ELEMENT_COMMON_DEFAULTS:
        assert key in elem
    for key in PARAM_DEFAULTS["glow"]:
        assert key in elem["params"]
    assert preset["global"]["intensity"] == 1.0
    assert preset["global"]["tint"] == [1.0, 1.0, 1.0]


def test_all_types_validate():
    preset = validate_preset(
        {"schema_version": 1, "elements": [{"type": t} for t in ELEMENT_TYPES]}
    )
    assert len(preset["elements"]) == len(ELEMENT_TYPES)


def test_unknown_element_type_names_offender_and_valid_types():
    with pytest.raises(ValueError) as exc:
        validate_preset({"schema_version": 1, "elements": [{"type": "sparkle"}]})
    msg = str(exc.value)
    assert "sparkle" in msg
    for t in ELEMENT_TYPES:
        assert t in msg


def test_missing_schema_version_fails():
    with pytest.raises(ValueError, match="schema_version"):
        validate_preset({"elements": []})


def test_future_schema_version_fails():
    with pytest.raises(ValueError, match="unsupported schema_version"):
        validate_preset({"schema_version": 2, "elements": []})


def test_missing_elements_fails():
    with pytest.raises(ValueError, match="elements"):
        validate_preset({"schema_version": 1})


def test_unknown_top_level_key_warns_but_passes():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        validate_preset({"schema_version": 1, "elements": [], "bogus_key": 1})
    assert any("bogus_key" in str(w.message) for w in caught)


def test_unknown_element_key_warns_but_passes():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        preset = validate_preset(
            {"schema_version": 1, "elements": [{"type": "ring", "wobble": 2}]}
        )
    assert any("wobble" in str(w.message) for w in caught)
    assert preset["elements"][0]["type"] == "ring"


def test_out_of_range_values_fail():
    with pytest.raises(ValueError, match="hollow"):
        validate_preset(
            {"schema_version": 1,
             "elements": [{"type": "iris", "params": {"hollow": 1.0}}]}
        )
    with pytest.raises(ValueError, match="blades"):
        validate_preset(
            {"schema_version": 1,
             "elements": [{"type": "iris", "params": {"blades": 2}}]}
        )
    with pytest.raises(ValueError, match="dispersion_samples"):
        validate_preset(
            {"schema_version": 1,
             "elements": [{"type": "ring", "dispersion_samples": 2}]}
        )
    with pytest.raises(ValueError, match="count"):
        validate_preset(
            {"schema_version": 1, "elements": [{"type": "ring", "count": 0}]}
        )


def test_spectral_defaults_high_dispersion():
    preset = validate_preset({"schema_version": 1, "elements": [{"type": "spectral"}]})
    elem = preset["elements"][0]
    assert elem["dispersion"] == 1.0
    assert elem["dispersion_samples"] == 7


def test_defaults_are_copies_not_shared():
    a = validate_preset({"schema_version": 1, "elements": [{"type": "glow"}]})
    a["elements"][0]["params"]["softness"] = 99.0
    a["global"]["tint"][0] = 5.0
    b = validate_preset({"schema_version": 1, "elements": [{"type": "glow"}]})
    assert b["elements"][0]["params"]["softness"] == 0.35
    assert b["global"]["tint"][0] == 1.0


def test_load_preset_bad_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        load_preset("{nope")


def test_values_override_defaults():
    preset = validate_preset(
        {"schema_version": 1,
         "global": {"intensity": 2.0},
         "elements": [{"type": "glow", "offset": 1.5,
                       "params": {"softness": 0.1}}]}
    )
    assert preset["global"]["intensity"] == 2.0
    assert preset["elements"][0]["offset"] == 1.5
    assert preset["elements"][0]["params"]["softness"] == 0.1
    assert preset["elements"][0]["params"]["falloff"] == 1.2
