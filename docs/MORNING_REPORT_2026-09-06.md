# Morning report

Everything below is committed, tested (311 tests), and live on the running
server — one instance, pid 39332. **Reload the browser once** (`Ctrl+Shift+R`)
and you have all of it.

---

## Do these two things first

**1. In your video-lab Flare Render, set `light_depth` to 0.1.** Your saved
workflow still stores `0.0`, and that is the single cause of the blank frames
you kept seeing: at 0.0 the sun occludes *itself* against a normalised depth
map. Measured on your shot: 7 fully dark frames of 60 at 0.0, **1 at 0.1**.
New nodes default to 0.1 now; I didn't edit your saved file.

**2. Position mode `lock`, smoothing 0.85–1.0, and pull `travel` down until
the flare sits where you want it.** On your driving shot, measured on the
rendered pixels:

| | your `_00003` render | lock, smoothing 1.0 | + travel 0 |
|---|---|---|---|
| mean movement / frame | 0.0204 | **0.0028** | **0.0000** |
| jumps over 5% of frame | 2 | 0 | 0 |

---

## What happened overnight

### Stress battery: no defects

I wrote a 180-case battery and ran it: every position mode against single
frames, black and white clips, fp16, 8×8 frames, portrait, uneven chunking,
linear colour, mismatched depth, `travel`, sixteen lights, threshold extremes
and the `lights` input; plus determinism, tracker corners, `lock` corners,
every element type at parameter extremes, every shipped preset in three
modes, and every other node with awkward input.

**Zero product defects.** Every "failure" was validation correctly rejecting
bad input with a clear message. Performance: 5–8 ms per 720p frame in every
mode.

### One real bug, found by combining features

`travel` pulled a keyframed **anchor** toward the *light's* centre, so at
travel 0 both points landed in one place and the flare axis collapsed to
nothing. Fixed — the anchor is damped about its own mean — and pinned by a
test. The battery didn't catch it because it never combined `travel` with the
keyframe input; that combination is in the suite now.

### Where the render time goes

Your ten-element preset costs 61 ms/frame at 720p. Profiling says it's pure
field math per element *instance* — `anamorphic bars` (count 4) alone is
19 ms, `rays` 15, `ghost chain` 15 — not convolutions. The lever is evaluating
each element only inside its bounding box instead of full-frame, a 2–3× win.
That's an engine change I would not make overnight without eyes on the
output, so it's the top item for next time, not done.

### UI: (i) everywhere a name isn't enough

49 controls now carry a small **(i)** that explains them in one breath on
hover. Size and opacity got none, as you said. The ones that matter most:

- **pos** — where along the flare axis the element sits (0 light, 1 anchor,
  negative behind the light)
- **max lights** — a *cap*, not a target: one sun at 3 still gives one flare
- **smoothing / max jump / travel** — what each actually controls, in
  `lock` terms too
- **mask scene / mask floor** — why 0 keeps a lens plate still, and what the
  floor hides
- **feature px / search px** — the tracker's patch and speed limit
- all the trigger-rule controls, and the checkboxes (auto-rotate, screen
  space, fill frame)

The bubble is drawn outside the panel so scrolling never clips it, and
rebuilding the panel never leaves one behind. Verified in the DOM.

### Also landed

- `light_depth` default 0.0 → 0.1 for new nodes and in the studio template.
- README now covers `lock`, `point_track`, `travel`, `mask_floor`,
  `mask_scene`, and the `light_depth` trap — with the measured numbers.
- DECISIONS.md has the reasoning for every change tonight.

---

## The two features from last night, for reference

**`lock`** solves the whole clip at once (Viterbi over per-frame candidates,
then a fitted path). A sun can't hop to the far side of frame for four
frames and come back. On your footage: wrong-side frames 5 → 0.

**`point_track`** is the AE-style tracker you asked for: click a feature
with contrast on the picker and the light rides it; click a second and it
becomes the anchor, so the axis inherits rotation and scale. Sub-pixel
accurate (0.00 px mean error on known motion), ~1 ms/frame, invariant to
exposure changes. A blown highlight has no detail to match — track a nearby
edge.

---

## Commits tonight

| | |
|---|---|
| `4e2aa56` | point tracking |
| `9210e51` | overnight QA: travel anchor fix, light_depth default, tooltips, docs |

311 tests. Server restarted, single instance, new code and new JS confirmed
served.
