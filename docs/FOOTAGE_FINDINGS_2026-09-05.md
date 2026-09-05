# Findings from your footage

Everything below was measured on `test_ftg/test.mp4` and `test2.mp4`, 60
frames each, through the real node — not on synthetic clips.

---

## 1. Why your render "didn't change": 20 of 50 frames were black

Your `flarecore_video_pass_00001.mp4` has **no flare at all** in frames
11–17, 20–26 and 44–49. Not dim — zero. That is 40% of the clip, and it is
the loudest thing wrong with the render.

It is the **occlusion** system, not the flare. I reproduced it with a depth
stand-in for the shot (sky far, trees near) and got the same pattern:
occlusion swings 0 → 1.0 → 0 as the sun passes behind foliage.

The amplifier is `light_depth = 0`:

| `light_depth` | mean occlusion | frames fully dark |
|---|---|---|
| **0.00 (yours)** | 0.39 | **7 / 60** |
| 0.10 | 0.20 | 1 / 60 |
| 0.25 | 0.09 | 1 / 60 |

`light_depth = 0` means "the light is at infinity", and an occluder counts
if it reads just 0.1 nearer than that. On a per-batch-normalised depth map
the sky is never exactly 0, so **the sun starts occluding itself**.

**Do this:** raise `light_depth` to about **0.1**. Counter-intuitively,
leave `occlusion_radius` alone or raise it — shrinking it makes things
*worse* (a small disk lands entirely on one branch and reads 100% blocked;
a wider disk averages sky and branch and reads partial). At radius 0.01 you
get 20 dark frames, at 0.10 you get 3.

## 2. "Detect jumps around like crazy" — found and fixed

Measured on your driving shot, the detected light moved a mean of **0.26 of
frame height every single frame**, with 25 of 59 transitions over 5%.

The cause: your sky is **clipped**. Frame 0 has **7783 pixels at exactly
1.0**. Ranking candidates by brightness therefore decides nothing, and the
sort fell through to its positional tie-break — so the light landed wherever
the plateau's shape put the topmost-leftmost white pixel that frame.

Two things that did *not* work, both measured: ranking by energy (just as
flat inside a saturated region) and blurring (a uniform plateau convolved
with a smaller kernel is still uniform in the middle).

What works is measuring the region's **centroid** with a window wide enough
to reach its edges — a gaussian-weighted centroid at sigma 0.25 of frame
height. On a synthetic disk it lands within 0.008 of true centre where a
0.08H window was 0.05 off, and a rival source 0.28H away tugs it by 0.004.

**Result, on your footage, measured on the rendered pixels:**

| | before | after |
|---|---|---|
| mean travel per frame | 0.2597 | **0.0434** |
| jumps over 5% of frame height | 25 | **6** |

Tracking deliberately does **not** use this. It is fed a pool of
fine-grained candidates and associates them itself, and it tracks
measurably *worse* when that pool is smoothed into regions first (max jump
0.048 → 0.065, and it began dropping frames). So this is per-frame `detect`
only.

## 3. Use `track`, not `detect`, for any clip

The single biggest quality lever, measured on your driving shot:

| mode | mean travel | jumps > 5% |
|---|---|---|
| detect (per-frame) | 0.0434 | 6 |
| **track** | **0.0099** | **0** |

Per-frame detection judges every frame independently by definition, so with
two rival bright regions it will always be able to flip. `track` is the same
detector plus identity through the clip. Your studio is already set to
`track` — keep it there.

## 4. Recommended settings for these two clips

Swept threshold × max_jump × smoothing on both.

**Driving shot** — best was `threshold 0.5, max_jump 0.05, smoothing 0.85`:
mean travel **0.0053**, zero big jumps. The shipped defaults (0.8 / 0.06 /
0.65) already give zero big jumps at mean 0.0103, so tuning roughly halves
the residual jitter.

**Dot matte** — the threshold matters in the opposite direction. It is
relative to the brightest thing in the clip, and raising it drops the dot:
threshold 0.8 loses 15 frames, 0.9 loses 30. Go *lower*, not higher.

## 5. Not a bug: the dot matte's empty frames

`test2.mp4` detects nothing in 15 of the first 60 frames. That is the
footage — per-frame peak brightness drops to 0.0056, i.e. the clip opens
with black lead-in frames before the dot appears. Lowering the threshold
recovers exactly one. Nothing to fix.

## 6. Lens dirt on the real shot

The `mask_scene` fix holds up on real footage: per-frame change in the
plate **halves**, 0.0186 → 0.0090, going from `mask_scene 1` to `0`. The
remainder is the light pool correctly following the tracked sun.

## 7. Performance

60 frames of 720p through your full 10-element preset:

| mode | time | per frame | peak VRAM |
|---|---|---|---|
| manual | 4.2 s | 70 ms | 9.3 GB |
| detect | 4.3 s | 72 ms | 9.3 GB |
| track | 4.9 s | 82 ms | 9.3 GB |

Detection and tracking cost ~2–12 ms/frame — the render dominates. No
optimisation needed here.

---

## Changed in the code

- **Region-based detection** for per-frame `detect` (commit `3978c50`),
  with tests pinning that it finds a plateau's centre, resists a rival, and
  leaves tracking's candidate pool untouched.
- **Tooltips that carry the measurements** (`eaf6d9d`): `light_depth` now
  explains the self-occlusion trap with the 7-of-60 number, and the `detect`
  hint says it is per-frame and points at `track` for clips.

283 tests passing.

## What I did not change, and why

I did not touch the occlusion defaults. The blinking is the system doing
what it says on a shot where the sun genuinely goes behind trees — the
problem is that `light_depth = 0` makes it trigger far too easily, and that
is a per-shot setting, not a default I can pick for every clip. If you want
a flare that dims rather than disappears when blocked, say so and I will add
an `occlusion_strength` control — that is a look decision and yours to make.
