# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.detect import detect_lights, linear_luminance
from flare.occlude import occlusion_factor


def frame_with_spot(h=64, w=96, cy=20, cx=70, brightness=1.0):
    img = torch.zeros(1, h, w, 3)
    img[0, cy, cx] = brightness
    img[0, cy - 1:cy + 2, cx - 1:cx + 2, :] += brightness * 0.3
    img[0, cy, cx] = brightness
    return img


class TestDetect:
    def test_finds_single_peak_subpixel(self):
        img = frame_with_spot()
        lights = detect_lights(img, threshold=0.5, max_lights=4)
        assert len(lights) == 1 and len(lights[0]) == 1
        light = lights[0][0]
        assert abs(light["u"] - (70 + 0.5) / 96) < 0.02
        assert abs(light["v"] - (20 + 0.5) / 64) < 0.02
        assert light["brightness"] == pytest.approx(1.0)

    def test_below_threshold_ignored(self):
        img = frame_with_spot(brightness=0.3)
        lights = detect_lights(img, threshold=0.8)
        assert lights[0] == []

    def test_brightest_first_and_max_lights(self):
        img = torch.zeros(1, 64, 96, 3)
        img[0, 10, 10] = 0.9
        img[0, 50, 80] = 1.0
        lights = detect_lights(img, threshold=0.5, max_lights=2, min_separation=0.05)
        assert len(lights[0]) == 2
        assert lights[0][0]["brightness"] > lights[0][1]["brightness"]
        lights = detect_lights(img, threshold=0.5, max_lights=1)
        assert len(lights[0]) == 1
        assert lights[0][0]["brightness"] == pytest.approx(1.0)

    def test_min_separation_suppresses_neighbors(self):
        img = torch.zeros(1, 64, 96, 3)
        img[0, 30, 40] = 1.0
        img[0, 30, 44] = 0.9  # 4px away
        lights = detect_lights(img, threshold=0.5, max_lights=4, min_separation=0.2)
        assert len(lights[0]) == 1

    def test_batch_frames_independent(self):
        a = frame_with_spot(cx=20)
        b = frame_with_spot(cx=70)
        img = torch.cat([a, b], dim=0)
        lights = detect_lights(img, threshold=0.5)
        assert lights[0][0]["u"] < 0.3
        assert lights[1][0]["u"] > 0.6

    def test_deterministic_ordering_on_ties(self):
        img = torch.zeros(1, 64, 96, 3)
        img[0, 10, 50] = 1.0
        img[0, 40, 20] = 1.0
        a = detect_lights(img, threshold=0.5, max_lights=2, min_separation=0.05)
        b = detect_lights(img, threshold=0.5, max_lights=2, min_separation=0.05)
        assert a == b
        # equal brightness: lower y first
        assert a[0][0]["v"] < a[0][1]["v"]

    def test_luminance_weights(self):
        img = torch.zeros(1, 2, 2, 3)
        img[0, 0, 0] = torch.tensor([1.0, 0.0, 0.0])
        img[0, 0, 1] = torch.tensor([0.0, 1.0, 0.0])
        lum = linear_luminance(img)
        assert lum[0, 0, 0] == pytest.approx(0.2126)
        assert lum[0, 0, 1] == pytest.approx(0.7152)


class TestOcclude:
    """Occlusion compares the scene against an explicit light_depth.

    Default light_depth=0.0 models a light at infinity (sun/sky), which is
    the dominant flare case.
    """

    def sky(self, h=64, w=64):
        """A far-everywhere map: sky at depth 0, nothing occluding."""
        return torch.zeros((h, w))

    def test_unoccluded(self):
        assert occlusion_factor(self.sky(), 0.5, 0.5, radius=0.05) == 0.0

    def test_fully_occluded_even_when_covering_the_light(self):
        # The regression this model exists for: an occluder covering the
        # light's own pixel must still read as fully blocking.
        depth = self.sky()
        depth[:, :] = 0.9
        occ = occlusion_factor(depth, 0.5, 0.5, radius=0.2)
        assert occ == 1.0

    def test_partial_occlusion_smooth(self):
        depth = self.sky()
        depth[:, :32] = 0.9  # left half covered by a nearer object
        occ = occlusion_factor(depth, 0.5, 0.5, radius=0.2)
        assert 0.0 < occ < 1.0

    def test_monotonic_as_occluder_advances(self):
        # Sweeping an occluder across the light must not pop: occlusion
        # increases monotonically and ends fully blocked.
        prev = -1.0
        for edge in range(0, 65, 8):
            depth = self.sky()
            depth[:, :edge] = 0.9
            occ = occlusion_factor(depth, 0.5, 0.5, radius=0.25)
            assert occ >= prev - 1e-6, f"occlusion dropped at edge={edge}"
            prev = occ
        assert prev == 1.0

    def test_invert_depth(self):
        # near-is-black map: an occluder sits at a low value
        depth = torch.ones((64, 64))
        depth[:, :] = 0.05
        occ_wrong = occlusion_factor(depth, 0.5, 0.5, radius=0.2, invert=False)
        occ_right = occlusion_factor(depth, 0.5, 0.5, radius=0.2, invert=True)
        assert occ_wrong == 0.0
        assert occ_right == 1.0

    def test_farther_object_does_not_occlude(self):
        depth = self.sky()
        depth[:, :32] = 0.05  # within margin of the light's own depth
        assert occlusion_factor(depth, 0.5, 0.5, radius=0.2) == 0.0

    def test_light_depth_selects_which_objects_occlude(self):
        # A mid-scene light: only things nearer than it should block.
        depth = self.sky()
        depth[:, :] = 0.5
        far_light = occlusion_factor(depth, 0.5, 0.5, radius=0.2, light_depth=0.0)
        near_light = occlusion_factor(depth, 0.5, 0.5, radius=0.2, light_depth=0.9)
        assert far_light == 1.0   # light behind the 0.5 layer -> blocked
        assert near_light == 0.0  # light in front of it -> clear

    def test_margin_tolerates_noise(self):
        depth = self.sky() + 0.02  # small uniform noise floor
        assert occlusion_factor(depth, 0.5, 0.5, radius=0.2) == 0.0

    def test_device_dtype_preserved(self):
        depth = torch.zeros((32, 32), dtype=torch.float32)
        occ = occlusion_factor(depth, 0.5, 0.5)
        assert isinstance(occ, float)


class TestOffFrameOcclude:
    """A light outside the frame -- follow mode's sun above the top edge, or
    a light the camera has panned off -- has no depth under it. The sample
    disk must not be clamped into the map, or the light is judged by
    whatever lines the nearest edge."""

    def test_a_light_outside_the_map_is_not_occluded(self):
        near_everywhere = torch.full((120, 160), 0.9)
        assert occlusion_factor(near_everywhere, 0.5, -0.08, radius=0.03,
                                light_depth=0.1) == 0.0

    def test_foliage_along_the_top_edge_does_not_block_a_sun_above_it(self):
        depth = torch.zeros(120, 160)
        depth[:8, :] = 0.9                       # canopy in the top rows only
        assert occlusion_factor(depth, 0.5, -0.08, radius=0.03,
                                light_depth=0.1) == 0.0

    def test_in_frame_samples_of_a_straddling_light_still_count(self):
        near_everywhere = torch.full((120, 160), 0.9)
        # the disk is half above the frame; what IS in the map is all near
        assert occlusion_factor(near_everywhere, 0.5, 0.0, radius=0.05,
                                light_depth=0.1) == 1.0


class TestClippedSky:
    """A blown-out sky is a plateau thousands of pixels wide, all at exactly
    1.0. The brightest PIXEL there is an arbitrary tie-break that moves with
    compression noise, so per-frame detection teleports across frame. Picking
    by region instead gives one stable point."""

    def _clip(self, frames=12, h=180, w=320, seed=0):
        """A big saturated blob, dead still, with a little sensor noise."""
        g = torch.Generator().manual_seed(seed)
        ys, xs = torch.meshgrid(torch.arange(h).float(),
                                torch.arange(w).float(), indexing="ij")
        blob = (((xs - 0.7 * w) ** 2 + (ys - 0.25 * h) ** 2) < (0.16 * h) ** 2)
        out = torch.zeros(frames, h, w, 3)
        for i in range(frames):
            img = blob.float() * 1.4                      # over 1.0 -> clips
            img = img + torch.rand((h, w), generator=g) * 0.02
            out[i] = img.clamp(0, 1).unsqueeze(-1).expand(-1, -1, 3)
        return out

    def _travel(self, lights):
        import math
        pts = [(f[0]["u"], f[0]["v"]) for f in lights if f]
        assert len(pts) == len(lights), "the light was lost"
        return max(math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(pts, pts[1:]))

    def test_region_selection_resists_a_rival_source(self):
        """The centroid window is wide enough to find a plateau's middle, so
        it must not be so wide that a second source drags the answer off."""
        clip = self._clip()
        h, w = clip.shape[1], clip.shape[2]
        ys, xs = torch.meshgrid(torch.arange(h).float(),
                                torch.arange(w).float(), indexing="ij")
        rival = ((xs - 0.42 * w) ** 2 + (ys - 0.30 * h) ** 2) < (0.05 * h) ** 2
        clip[:, rival] = 1.0
        u = detect_lights(clip, threshold=0.75, max_lights=1,
                          region_sigma=0.02)[0][0]["u"]
        assert abs(u - 0.7) < 0.03, f"the rival pulled the light to {u:.3f}"

    def test_region_selection_holds_still(self):
        clip = self._clip()
        lights = detect_lights(clip, threshold=0.75, max_lights=1,
                               region_sigma=0.02)
        assert self._travel(lights) < 0.005

    def test_region_selection_lands_on_the_blob(self):
        lights = detect_lights(self._clip(), threshold=0.75, max_lights=1,
                               region_sigma=0.02)
        u, v = lights[0][0]["u"], lights[0][0]["v"]
        assert abs(u - 0.7) < 0.05 and abs(v - 0.25) < 0.05, (u, v)

    def test_brightness_is_still_the_pixel_value(self):
        """Ranking by region must not change what brightness reports."""
        lights = detect_lights(self._clip(), threshold=0.75, max_lights=1,
                               region_sigma=0.02)
        assert lights[0][0]["brightness"] > 0.9

    def test_default_is_unchanged(self):
        clip = self._clip()
        assert detect_lights(clip, threshold=0.75, max_lights=1) ==                detect_lights(clip, threshold=0.75, max_lights=1, region_sigma=0.0)
