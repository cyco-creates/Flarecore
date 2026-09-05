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


---

# Round two (you were still up)

Everything below is in commit `e16621a`, 315 tests, server restarted, one
instance. Reload the browser once.

## Prompts — rewritten from research, not taste

Two agents worked in parallel while I did the engineering; I re-verified
everything they produced (keys, brands, validation, renders) before shipping.

What the research on Krea 2 actually says, with sources in DECISIONS.md:
it is a single-stream DiT behind a Qwen3-VL encoder, so it wants
**natural-language prose of 30–80 words**, **subject first** (word order is
emphasis, weighting syntax does nothing), it has **no negative channel** at
the CFG it runs at (say what *is* in frame), and it responds to **named
optical looks** and raw-photographic cues rather than quality tags.

Every prompt now: leads with a macro photograph of an optical phenomenon,
names the physics (veiling glare, halation, coating tints, blade counts that
give the right spike counts, thin-film interference, cylindrical elements),
and carries **one specific physical imperfection** — a broken ring, a missing
ghost in a chain, a smudge through a disc, uneven spikes, a run droplet — so
no element is perfect. **40 rewritten, 35 new (75 total)**, every original
key preserved, no brand names anywhere.

## Presets — 22 now

Ten new looks, each modelling a named lens behaviour: `anamorphic_teal_2x`,
`vintage_uncoated`, `clean_modern_prime`, `long_telephoto`, `sodium_street`,
`neon_night`, `film_halation`, `headlight_bloom`, `underwater_caustic`,
`cheap_zoom_haze`. Five use trigger rules. All validated and render-checked.

## Tracking lives in Flare Render now

- `hold`, `fade` and `search radius` sit beside smoothing and max jump.
- The search region is **centred on the picker's light point** — drag the
  point to move the ring. Measured: with a brighter rival across frame, the
  fence holds the flare on the intended light.
- **Flare Track is retired** (7 nodes now). Nothing it did is gone.
- After every render the picker **draws the path the light took** — orange
  for the light, cyan for the anchor — so you can judge a track before
  trusting it.
- **`bake to path`** appears after a render in any tracked mode: one click
  turns the track into a drawn motion path, switches to path mode, and you
  fix the one frame the tracker got wrong by dragging a point.

## Flare lab depth

The studio's flare lab now has its own Depth Anything wired into the still
render node. That's what you were adding by hand.

## Look

Sharper corners throughout (12→6, 8→4, 5–7→3), a rule down the left of each
element row, **chips 30→44 px**, and a **220 px hover preview** of any
picked texture with its filename. And one real bug found by driving the DOM:
adding a lens_dirt texture via **+ add → library** skipped the lens-plate
defaults (the gallery pick had them) — the full-frame wash again. Fixed on
both paths.


---

# Round three: the tracking rewrite

Commit `b9c88ac`, 331 tests, server restarted, one instance. **Reload the
browser** and then read the first section below before anything else.

## What was actually wrong — and why my numbers missed it

I rendered contact sheets of every mode over your driving shot and looked at
them the way you do. One glance:

**The sun is above the top edge of the frame.** Every detector was placing the
light on the visible *sky patch* below it — the wrong place — and because the
flare's anchor sits at frame centre, every wobble of that patch as branches
crossed it swept the ghost chain across the picture. My metrics said the
light was "steady to 0.007". It was steadily in the wrong place, and the
ghosts were doing the moving. No brightness detector can find a light that
isn't in the picture.

What *is* in the picture is how everything moves. A sun is at infinity: it
moves only with camera rotation, and camera rotation is exactly what the far
features in the frame reveal. So the fix is the one compositors use — **track
the camera, not the sun** — and a research agent's survey of Nuke/AE practice
came back saying the same thing independently.

## `follow` — the mode for this shot

1. Set position mode to **`follow`**.
2. **Drag the light to where the sun really is — above the frame.** The
   picker now has room around the picture; put it at roughly (0.88, −0.08)
   for this clip.
3. Render.

The camera carries the light. Nothing is detected, so an off-frame sun, a
clipped sky and a canopy of branches cannot touch it. On your shot: max step
**0.0035**, the light above the frame in every tile, the ghost bars holding
one spot across the whole clip.

Under the hood: Shi-Tomasi corners chosen *per grid cell* (sharp trunks and
road otherwise take every slot and the sky is never tracked at all — I
measured all 48 corners landing on the foreground), batched pyramidal
Lucas–Kanade at 35 ms/frame, and a **rigid** fit — rotation and shift, scale
fixed — because a free scale reads driving forward as a zoom and pushes an
off-frame light out a little more every frame (drift 0.56 of the frame in
60 frames with scale free, 0.02 with it fixed).

## Every other mode, anchored to the scene

`detect`, `track`, `track_dots` and `lock` now carry the light from its most
confident frame by the camera's motion, and only the detector's
*disagreement* with that is smoothed — median-filtered, clipped to a few
percent of frame so a hop to a rival source cannot drag it, then low-passed.
Measured on your shot, mean step per frame / worst step:

| mode | before tonight | now |
|---|---|---|
| detect | 0.26 / 0.93, teleports | **0.0016 / 0.0041** |
| track | 0.0099 / 0.048 | **0.0018 / 0.0047** |
| lock | 0.0073 / 0.059 | **0.0014 / 0.0053** |
| follow | — | **0.0017 / 0.0035** |

A new **`scene lock`** control sets the strength: **1** for a sun (it moves
only with the camera), **0** for a light that moves on its own — headlights
crossing frame, a torch. A matte with no scene in it is left alone
automatically, so your dot matte keeps its full travel (verified: unchanged).

## Settings applied when you pick a mode

Choosing a mode now sets it up with the values that measured best —
threshold 0.6, smoothing 0.85–0.9, scene lock 1 for the sun modes; scene
lock 0 and max lights 4 for dots. You can still change anything afterwards.

## Your saved workflow

I set `light_depth` to 0.1 and `occlusion_radius` to 0.03 on both render
nodes (backup `.bak3`). Those were the blank frames. Everything else in your
file is untouched.

## Also in this round

- The **point tracker** was rebuilt: pyramidal LK frame-to-frame with a
  forward–backward confidence check, drift correction and NCC re-acquisition.
  A feature that zooms ×1.6 now tracks to 1 px; the old one died at frame 12.
  Two of the "failures" I chased turned out to be features leaving the frame.
- The **element hover preview** you asked for is back, and better: every
  element — procedural ones included — renders **solo, as configured**
  (colour, scale, count, blur) into a 260 px popover, via a small CPU render
  cached by content. ~10–17 ms.
