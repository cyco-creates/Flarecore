"""Visible regressions: ray direction and low-precision input stability."""
import pytest
import torch

from flare.elements import glint, iris
from flare.schema import PARAM_DEFAULTS, validate_preset
from test_nodes import run_node


def test_single_ray_has_no_opposite_lobe():
    u = torch.tensor([-0.5, 0.5])
    v = torch.zeros_like(u)
    p = dict(PARAM_DEFAULTS["glint"], points=1, length_jitter=0)
    actual = glint(u, v, p)
    assert actual[0] == 0
    assert actual[1] > 0.1


def test_rounded_iris_circle_is_independent_of_blade_count():
    axis = torch.linspace(-1.1, 1.1, 97)
    v, u = torch.meshgrid(axis, axis, indexing="ij")
    triangle = iris(u, v, dict(PARAM_DEFAULTS["iris"], blades=3, roundness=1))
    octagon = iris(u, v, dict(PARAM_DEFAULTS["iris"], blades=8, roundness=1))
    torch.testing.assert_close(triangle, octagon, atol=1e-6, rtol=1e-6)
    legacy = iris(u, v, dict(PARAM_DEFAULTS["iris"], blades=3))
    assert triangle.sum() > legacy.sum() * 1.5


@pytest.mark.parametrize("kind", ["iris", "spectral"])
@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
def test_invalid_roundness_rejected(kind, value):
    with pytest.raises(ValueError, match="roundness"):
        validate_preset({"schema_version": 1, "elements": [
            {"type": kind, "params": {"roundness": value}}]})


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
def test_half_inputs_match_float_math_with_output_cast(dtype):
    # Wide grid and thin features expose coordinate quantization. Compare
    # against exactly the same quantized plate promoted to float32.
    image = torch.linspace(0, 0.8, 1536).reshape(1, 1, 1536, 1)
    image = image.expand(1, 12, 1536, 3).to(dtype)
    actual = run_node(image, clamp_output=False)
    reference = run_node(image.float(), clamp_output=False)
    for result, expected in zip(actual, reference):
        assert result.dtype == dtype
        assert torch.isfinite(result).all()
        torch.testing.assert_close(result, expected.to(dtype), rtol=0, atol=0)
