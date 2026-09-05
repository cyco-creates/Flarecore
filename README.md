# comfyui-flarecore

Procedural lens flare rendering for ComfyUI. Pure PyTorch math on a coordinate
grid — no OpenGL, no textures, no models, no network calls. Works headless on
CUDA, MPS, and CPU.

## What it does

- Renders a stack of parametric flare elements (glow, iris ghosts, anamorphic
  streaks, rings, hoops, starbursts, spectral rings, procedural lens orbs,
  and your own photographed textures) along a physically motivated flare
  axis from the light position through a movable anchor.
- Every procedural element can be made imperfect (`irregular`): uneven ring
  brightness, wobbly iris edges, rays of differing length and gain — seeded,
  so it holds still across a video.
- All math happens in linear light; sRGB decode on input, encode on output.
- HDR internally (intensities may exceed 1.0); clamping is optional and only
  applied at the final encode.
- Automatic light detection from image luminance, and depth-based occlusion so
  flares fade when the light source is blocked.

## Nodes

- **FlareRender** — renders a flare from a JSON preset over an image. Outputs
  the composite, the flare pass over black (for external compositing in Nuke,
  Resolve, After Effects), and a luminance mask.
- **FlarePresetLoader** — picks a preset file from `presets/` and outputs its
  JSON as a string. Drop new `.json` files in; the list rescans without
  restarting ComfyUI.
- **FlareDepthAdapter** — conditions a depth map for occlusion use: normalize
  range, flip near/far convention, remap levels, blur edges. Pure tensor math.
- **FlareElementPrompts / FlareTexturePrepare / FlareElementSave /
  FlareElementPicker** — the element forge: an editable prompt bank
  (`prompts/element_prompts.json`), post-processing that turns a generated
  image into a compositing-safe element (black floor, auto-center, border
  feather), and the element library under `elements/<category>/`.

## The editor

FlareRender carries its own UI:

- **Point picker** — drag the orange **light** and the cyan **flare anchor**
  directly on the last rendered frame. Elements sit at
  `P + t · (anchor − P)`, so the ghost chain aims at the anchor and its
  spacing grows with the distance between the two points.
- **Stack editor** — add, reorder, duplicate, solo and delete elements; every
  row has sliders for position along the axis, size, opacity and blur plus a
  colour swatch, and the twirl-down exposes everything else (irregularity,
  dispersion, count chains, stretch, translation locks, per-type parameters,
  trigger rules). Clicking an element's name opens the gallery of your own
  elements in that family; double-click renames; `⋯` copies and pastes
  settings between rows. Undo/redo (`↶ ↷`, Ctrl+Z/Y) covers every change.
  It reads and writes the `preset_json` widget, so hand-edited JSON and the
  editor stay in sync. `save…` writes into `presets/`; `presets ▾` loads any
  shipped or saved look, or **merges** one into the current stack.
- **lens ▾** on the global row: chromatic fringe, per-light flicker, and edge
  fade (a lens hood — lights that leave the frame fade out).

## Rule-based animation

Elements can react to where the light is without keyframes:

- **Triggers** — each element's twirl-down has a trigger rule. As the light
  (or the element itself) reaches the frame **border**, the **centre**, or
  comes near the **light**, the element adds brightness, scales, rotates and
  shifts colour, over a feathered range with linear, smooth or exponential
  falloff. Set an element's opacity to 0 and it exists only while its rule
  fires — a gleam that blooms as the sun leaves frame, a ring flash when a
  headlight crosses centre, a spark that only lights up when a ghost passes
  close to the source.
  Rows with a rule carry a **red edge** in the editor, and `preview region`
  paints the trigger area over the picker in red while you drag its range —
  the same field the engine evaluates.
- **Translation locks** (`move x / move y`) — how much of the light's motion
  an element follows per screen axis. `move y = 0` gives the horizontal-only
  ghost bars of an anamorphic lens; fractional values give the loose,
  not-quite-on-the-axis drift of real flares.
- **Flicker** — seeded per-light brightness noise over the frame index, so a
  row of stage lamps never pulses in unison.

## Lens character

- `global.aspect` widens every element along the screen's horizontal axis
  regardless of the flare angle (anamorphic squeeze).
- `global.fringe` adds lateral chromatic aberration to the finished flare —
  red outward, blue inward, growing toward the corners.
- **Completion** on rings, hoops, glints and spectral rings limits them to a
  feathered arc; rotation aims the arc.
- **Lens orbs** — procedural out-of-focus specks locked to the lens, each lit
  by its distance to the light (`illumination`). Duplicate with a new seed and
  a different size for layered grime.
- `scene_color` on FlareRender tints each light's flare by the plate colour
  at the source, so a sunset sun flares warm and a sodium lamp flares orange.
- Thin rays and rings are anti-aliased at sub-pixel widths with their energy
  conserved: a hairline glint keeps its brightness at every resolution
  instead of shimmering between frames.

## Video

In ComfyUI a video is an image batch, and every node here is batch-native.
**One node does the work**: FlareRender tracks, conditions depth and picks
where the light comes from, so the graph is Load Video -> Flare Render ->
Combine. `position_mode` chooses:

- `manual` — drag the light on the picker.
- `detect` — the brightest spot, each frame on its own.
- `track` — the same, followed through the clip. For footage. If the flare
  wanders between nearby lights, lower `track_max_jump`.
- `track_dots` — a black plate with white dots as a control layer: **every
  dot gets its own flare** with its own identity across the clip. The
  threshold is relative to the brightest thing in the clip, so a matte that
  never reaches pure white still works. Raise `detect_max_lights` to the
  number of dots you expect and take `flare_pass` to composite over your
  real footage.
- `path` — **draw the light's route**. Choosing this mode arms the picker:
  click to drop a point, drag to move one, shift-click to remove. The light
  travels the whole path across the clip, so put points closer together where
  you want it to slow down. The anchor still drags as usual.

The mode lives in a **light source** dropdown at the top of the editor
panel, and each mode shows only its own controls — thresholds and tracking
for the detect/track modes, a point count and `clear path` for path, nothing
but a hint for manual. The picker follows: the light handle greys out
whenever something other than you is placing it, and clicking only draws a
path when path mode is selected.

Wire a raw depth model straight into `depth` and set `depth_normalize` to
`per_batch`; `depth_blur` and `depth_temporal_smooth` replace the adapter for
ordinary use. FlareTrack, FlareDepthAdapter and FlareLightsSwitch still exist
for graphs that want the pieces separately.

The parts that make flares hold together across frames:

- **FlareTrack** turns per-frame detections into stable tracks: one identity
  per light across the clip (crossing lights don't swap), smoothed zero-phase
  positions (no lag, no jitter), fade in/out instead of strobing at the
  detection threshold, and velocity coasting — a light that vanishes behind
  an occluder keeps travelling, so depth occlusion completes its fade and the
  track re-acquires the light on the far side. Outputs `FLARE_LIGHTS` plus a
  colour-coded overlay for checking the track.
  `detect_max_lights` caps how many **flares** exist, not how many candidates
  are examined: many more are detected than kept, because having a spare
  candidate near the light you are following is what stops a busy frame
  (dappled light through trees, a row of lamps) from handing the flare to
  whichever blob happens to win that frame's brightness contest.
  If it locks onto the wrong light, set `search_radius` above 0 and put
  `search_u`/`search_v` on the one you want — only that region is searched,
  and the overlay draws a dashed ring showing where.
  **If the flare wanders between nearby sources, lower `max_jump`.** It is
  the gate deciding what may continue a track, so size it to how far the
  light actually travels between frames (usually well under 0.03 of frame
  height) rather than to how far apart the lights are. Association measures
  from where the track is predicted to be, so a small gate still follows fast
  motion.
- **FlareKeyframes** hand-animates instead: `frame: u,v` paths for the light
  and optionally the flare anchor, linear or eased.
- **FlareLightsSwitch** decides which one drives the flare, so a graph can
  keep both wired and switch with a dropdown:
  - `detect (tracked)` — Flare Track owns the light; the picker's light
    handle greys out and says so.
  - `keyframes` — Flare Keyframes drives **both** points by hand:
    `light_keys` and `anchor_keys`, each `frame: u,v; frame: u,v`.
  - `manual (render node's points)` — nothing is passed through, so Flare
    Render uses its own `position_mode` and you drag the light and the
    anchor on the picker as usual.
- **FlareRender**'s optional `lights` input consumes any of them, and reports
  back which control actually placed the light so the picker never shows a
  handle that moves nothing. Its `occlusion_smooth` spreads occlusion changes
  for tracked lights across frames — a thin occluder becomes a fade, never a
  one-frame cut.
- **FlareDepthAdapter**'s `temporal_smooth` stills per-frame depth-model
  shimmer (zero-phase along the batch); keep `normalize` on `per_batch`.

The `flarecore_video_lab` workflow wires the whole chain from Load Video to
two rendered videos (composite and flare pass).

## Custom elements (element forge)

Shipped workflows (ComfyUI → Workflow → Browse Templates → flarecore):

- **flarecore_flare_lab** — the full playground: scene, depth occlusion,
  editor, and all outputs.
- **flarecore_video_lab** — video in, tracked/occluded flare video out, with
  the flare reacting to the light's travel (triggers, flicker, edge fade).
  Tracking, keyframes and manual placement are all wired; the Lights Switch
  picks one.
- **flarecore_trigger_lab** — rule-based animation with nothing to load:
  a keyframed light sweeps across a dark plate and the Trigger Showcase
  preset lights up on the border, at the centre, and near the light. Queue it
  and watch; then twirl a red-edged row open and preview its region.
- **flarecore_element_forge** — generate custom element textures with your
  local image model (wired for Krea2), condition them, and file them in the
  library at 2K with a black margin so nothing ever crops. Pick a prompt from
  the bank, queue, then click any element's name in the editor to swap in
  your new element. The bank's eight categories (glows, ghosts, rays,
  streaks, rings, hoops, caustics, lens_dirt) are the same families the
  editor's gallery filters by. The engine itself never runs a model; the
  `texture` element just samples your library through the same transform
  pipeline (dispersion included) as the procedural elements.

## Depth occlusion

This pack does not estimate depth — that needs a model, which the engine
deliberately has no part of. Feed `FlareRender.depth` from any depth source:

```
Load Image ──> Depth Anything V2 ──> Flare Depth Adapter ──> FlareRender.depth
           └────────────────────────────────────────────────> FlareRender.image
```

MiDaS, Zoe, Metric3D, LeReS and rendered Z-passes work equally well. The
adapter is optional but makes any source behave predictably.

Two controls decide the result:

- `light_depth` (on FlareRender) is where the light sits on the depth scale:
  `0.0` = infinitely far, which is right for a sun or sky light and is the
  default. Raise it for a light that sits mid-scene, so only things in front
  of it block the flare.
- `invert_depth` states the source's convention. Depth Anything, MiDaS and Zoe
  emit near-as-white, so leave it off; turn it on for near-as-black sources.
  It is never guessed.

`occlusion_radius` sets how gradually the flare fades as an object crosses the
light — about 20 px of travel at the `0.02` default on a 1024-wide frame, and
roughly 46 px at `0.08`. Raise it, and the adapter's `blur`, for a softer fade
on moving shots.

For video, leave the adapter's `normalize` on `per_batch`. Per-frame
normalization rescales each frame on its own range, so a static object's depth
drifts as other content enters and leaves the shot and the occlusion breathes.

## Preset format

Presets are versioned JSON (`schema_version: 1`). Every key has a default, so a
minimal preset is just:

```json
{ "schema_version": 1, "elements": [{ "type": "glow" }] }
```

See `presets/` for full examples (`cine_blue` is the default; `anamorphic_gold`,
`sun_natural` and `stage_spot` show move locks, triggers, orbs, completion and
flicker) and `flare/schema.py` for the complete key reference. Unknown element
types fail loudly; unknown extra keys warn.

## Notes for compositors

- `flare_pass` is the flare rendered over black, encoded to sRGB. With
  `clamp_output` off it is unclamped HDR. Adding the decoded pass over the
  decoded source in linear light reproduces the `image` output exactly.
- `add` is the physically correct blend mode. `screen` is provided as a
  soft-clipped convenience and operates on values clamped to [0, 1].

## License

Apache-2.0. Everything is procedural and original; no proprietary preset
formats are read or written.
