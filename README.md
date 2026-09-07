# comfyui-flarecore

Procedural lens flare rendering for ComfyUI. Pure PyTorch math on a coordinate
grid — no OpenGL, no models, no network calls. Photographed elements are
optional and sampled through the same transform pipeline. Works headless on
CUDA, MPS, and CPU.

## What it does

New here? Start with the [Studio guide](docs/STUDIO_GUIDE.md).
For reference documents and developer entry points, use the
[documentation index](docs/README.md).

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
- **FlareElementPrompts / FlareTexturePrepare / FlareElementSave** — the
  element forge: an editable prompt bank
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
  colour swatch. The twirl-down organizes the remaining controls into
  Shape & appearance, Position & orientation, Repeated elements,
  Masking & lens space, and Optical response sections.
  Sections remember their open state while editing. Clicking an element's name opens the gallery of your own
  elements in that family; double-click renames; `⋯` copies and pastes
  settings between rows. Undo/redo (`↶ ↷`, Ctrl+Z/Y) covers every change.
  It reads and writes the `preset_json` widget, so hand-edited JSON and the
  editor stay in sync. `save…` writes into `presets/`; `presets ▾` loads any
  shipped or saved look, or **merges** one into the current stack.
- **lens ▾** on the global row: chromatic fringe, per-light flicker, and edge
  fade (a lens hood — lights that leave the frame fade out).

## Rule-based animation

Elements can react to where the light is without keyframes:

- **Optical response** — position-driven curves control opacity, size,
  aspect, rotation, color and supported shape features. The older trigger
  editor is retired. Old rules still render for compatibility; affected
  elements show a notice and an explicit removal action.
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
- **Lens plates** (dirt, droplets, grime) sit on the front element: forge them
  with `frame = wide_16_9` (no crop, no centring, no feather) and give the
  element `fill_frame`, which spans the footage's own aspect whatever that is.
  They are revealed only where the light reaches — `light_mask` on the element
  decides how much, `mask_falloff` on the node decides how far. Two more
  per-element controls finish the job: `mask_floor` hides the element wherever
  the light's pool is dimmer than it, so dirt actually disappears in the dark
  parts of frame (the mask is max(scene, glow) and neither term ever reaches
  zero on its own), and `mask_scene` decides how much the scene's own bright
  areas reveal it as opposed to the light — at 0 a lens plate is revealed by
  the source alone and holds perfectly still instead of crawling with whatever
  drives past. Picking a lens_dirt texture from the library sets all of this.
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
- `detect_with_manual_offset` — detect, then shift by the picker's own
  offset from centre, for a light the detector finds slightly off.
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
- `lock` — **solve the whole clip at once**. Reads every frame before deciding
  and picks the light path with the least total travel, so a sun cannot hop to
  a rival bright region for a few frames and come back — the failure per-frame
  detection cannot see, because it only ever looks at one frame. `smoothing`
  then trusts a smooth fitted path over the remaining wobble; at 1 the light
  follows the fit exactly. Measured on a driving shot with a clipped sky:
  frames on the wrong side of frame 5 → 0, mean travel 0.26 → 0.007.
- `point_track` — **follow picture, not brightness**, the way a compositor's
  point tracker does. Click a feature with contrast on the picker and the
  light rides it; click a second and it becomes the anchor, so the flare axis
  takes the pair's rotation and scale too. Matching is normalized
  cross-correlation, so an exposure ramp or a light blowing out does not
  move it. A blown highlight has no detail to match — track a nearby edge
  instead. `feature px` is the patch size, `search px` the speed limit.

- `follow` — **track the camera, not the light.** Put the light where the
  source really is — the picker has room around the frame, so drag it
  *outside* if the sun is above the edge — and it is carried by the camera's
  motion, read from the whole picture (Shi-Tomasi corners, pyramidal
  Lucas–Kanade, a rigid fit weighted toward the far field). Nothing is
  detected, so an off-frame sun, a clipped sky and a canopy full of branches
  cannot touch it. The steadiest option for any shot where the light is not
  a clean dot.

Every detection mode is **anchored to the scene** the same way: the light is
carried from its most confident frame by the camera's motion, and only the
detector's disagreement with that is smoothed, so a frame that hops to a
rival source cannot drag the light. **`scene lock`** sets how strongly — 1
for a sun (it moves only with the camera), 0 for a light that moves on its
own, such as headlights crossing frame. A matte with no scene in it is left
alone automatically. Choosing a mode applies the settings measured best for
it, so a mode arrives ready to use.

Every tracked mode has a **`travel`** slider: 1 follows the tracked path,
0 pins the flare in one spot for the whole clip, and anything between keeps
the path with its excursion scaled down. Each light is damped about its own
centre, so several dots on a matte do not collapse together.

The mode lives in a **light source** dropdown at the top of the editor
panel, and each mode shows only its own controls — thresholds and tracking
for the detect/track modes, a point count and `clear path` for path, nothing
but a hint for manual. The picker follows: the light handle greys out
whenever something other than you is placing it, and clicking only draws a
path when path mode is selected.

Wire a raw depth model straight into `depth` and set `depth_normalize` to
`per_batch`; `depth_blur` and `depth_temporal_smooth` replace the adapter for
ordinary use. FlareDepthAdapter still exists for graphs that want the depth
conditioning separately. Tracking lives entirely in Flare Render now: hold,
fade and a search region (centred on the picker's light point) sit beside
smoothing and max jump, and after a render the picker draws the path the
light actually took, with a **bake to path** button that turns it into an
editable motion path.

The parts that make flares hold together across frames:

- **Tracking** (inside Flare Render) turns per-frame detections into stable
  tracks: one identity per light across the clip (crossing lights don't swap),
  smoothed zero-phase so it never lags, held for `track_hold` frames and faded
  over `track_fade` when lost. `search_radius` confines the search to a circle
  around the picker's light point, so a rival source across frame cannot steal
  the flare however bright it is.
- **FlareKeyframes** hand-animates instead: `frame: u,v` paths for the light
  and optionally the flare anchor, linear or eased.
- **FlareRender**'s `position_mode` decides what drives the flare -- manual,
  detect, track, track_dots or a drawn path -- and its optional `lights`
  input outranks all of them whenever something is connected. Leave it
  unconnected and the picker's own light and anchor take over. It consumes
  any of the sources above, and reports
  back which control actually placed the light so the picker never shows a
  handle that moves nothing. Its `occlusion_smooth` spreads occlusion changes
  for tracked lights across frames — a thin occluder becomes a fade, never a
  one-frame cut.
- **FlareDepthAdapter**'s `temporal_smooth` stills per-frame depth-model
  shimmer (zero-phase along the batch); keep `normalize` on `per_batch`.

The studio's video bench wires the whole chain from Load Video to two
rendered videos (composite and flare pass).

## Custom elements (element forge)

The prompt bank (`prompts/element_prompts.json`, 75 prompts across eight
families) is written for Krea 2: natural-language prose, subject first, the
optics named (veiling glare, halation, coating tints, blade counts, thin-film
interference), and one deliberate physical imperfection per element so nothing
comes out computer-perfect. Every prompt is editable in the forge panel.


Shipped workflows (ComfyUI → Workflow → Browse Templates → flarecore):

- **flarecore_studio** — the whole pack in one graph: three benches (element
  forge, flare lab, video lab) side by side, and a **Flarecore Studio**
  switch on the left. Click a bench and the other two grey out and mute, so
  Queue only ever runs the one you are working in.
- **flarecore_trigger_lab** — rule-based animation with nothing to load:
  a keyframed light sweeps across a dark plate and the Trigger Showcase
  preset lights up on the border, at the centre, and near the light. Queue it
  and watch. This is a compatibility demo for older trigger-based presets;
  use Optical response for new animation.

The forge bench's prompt node carries its own panel: pick a **category**,
then an **element**, and its bank prompt appears in an editable box — what
you see is exactly what queues. The **extra style** tail is appended only
while its toggle is on, and its dropdown offers real shooting conditions
from `prompts/element_styles.json`, grouped as lens era, capture medium,
lens condition, atmosphere, light source and filtration: uncoated pre-war
glass, colour reversal stock, a greasy fingerprint, thick fog, a
low-pressure sodium lamp, a black diffusion filter. Picking one fills the
box, which stays editable — type over it and the picker reads *custom*.
Add your own by editing the file; the panel rescans it. The old raw widgets still exist underneath and
still drive everything, so saved workflows and API use are unchanged. The
model and sampling chain is collapsed into a **Krea2 generator** subgraph —
double-click it to look inside; `seed` is exposed on the outside for quick
variations.

### Two generators, one switch

The forge can carry two image generators side by side — the local **Krea2
generator** subgraph and a hosted **GPT Image 2** node — with a **Flare
Generator Select** node choosing between them. Flip its `generator` widget
(`a` = Krea2, `b` = GPT Image 2) and the chosen branch feeds Flare Texture
Prepare while the other is muted: the switch's inputs are *lazy*, so the
unselected generator is never executed and the API one costs nothing while
you work locally. The editor also greys the muted branch so the disabled
generator looks disabled, and re-applies that after the studio bench switch
wakes the forge (which would otherwise un-mute everything in it). To swap
GPT Image 2 for any other generator, rewire its IMAGE output into the
switch's `image_b`; add a third by chaining a second switch.

Depth occlusion is wired in both Flare Lab and Video Lab. Supply your own
image or clip; these workflows do not include personal source footage.

- The forge itself — generate custom element textures with your
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
  `0.0` = infinitely far. An occluder counts if it reads 0.1 nearer than
  this, and on a normalised map the sky is never exactly 0 — so at `0.0` **the
  sun starts occluding itself** and the flare blinks out in clear sky. On a
  sun-through-trees shot the flare went fully dark in 7 frames of 60 at 0.0
  and in 1 at 0.1, which is why the default is now `0.1`. Raise it further
  for a light that sits mid-scene, so only things in front of it block the
  flare. Counter-intuitively, do not shrink `occlusion_radius` to fix
  blinking: a small disk lands entirely on one branch and reads 100%
  blocked, a wider one averages sky and branch and reads partial.
- `invert_depth` states the source's convention. Depth Anything, MiDaS and Zoe
  emit near-as-white, so leave it off; turn it on for near-as-black sources.
  It is never guessed.

`occlusion_radius` sets how gradually the flare fades as an object crosses the
light. It is a fraction of frame HEIGHT, so on a 1080-high frame the `0.02`
default samples a disc about 22 px across and `0.08` about 86 px — four times
the travel, not twice. Raise it, and the adapter's `blur`, for a softer fade
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

See `presets/` for full examples and `flare/schema.py` for the complete key
reference. Unknown element types fail loudly; unknown extra keys warn.

## The preset library

Built from a frame-by-frame survey of 44 cine lenses on a sweeping point
source. Each preset models an optical *family* rather than a product, and is
named for the behaviour. Two things the survey settled shape the library:
**geometry belongs to the optics and colour to the coating** — three
coatings of one lens share every shape and differ only in the streak — and
**ghost size scales with focal length**, which is what `global.scale` does,
so the `_tele` variants are the same stack scaled up.

Anamorphic: `ana_soft_bloom` (big cool bloom, ghosts that flash through one
narrow angle), `ana_hairline_classic` (full-width hairline, front-element
disc rimmed with spectral dust — also shipped as `cine_blue`, the default),
`ana_heavy_blue` (bimodal streak, the companion ghost's own streak below,
edge hotspots, spectral caps), `ana_clean_modern`, `ana_coated_blue` /
`_amber` / `_silver` / `_clear` (one geometry, four coatings),
`ana_dashed_lf`, `ana_vintage_lf` (two-tone streak, spectral ringed disc,
corner caustic), `ana_crescent`, `ana_double_streak`.

Spherical: `sph_vintage_coated`, `sph_uncoated_veil`, `sph_vintage_ring`,
`sph_modern_soft`, `sph_modern_clean`, `sph_designed_blue` (+ `_tele`),
`sph_golden_vintage`, `sph_epic_65`, `sph_jewel_lf`, `sph_zoom_sparkle`,
`sph_zoom_tele_warm`.

Scenario presets stay alongside them: `trigger_showcase`, `stage_spot`,
`sodium_street`, `neon_night`, `headlight_bloom`, `underwater_caustic`,
`film_halation`, `scifi_starburst`, `specimen_all_elements`.

### How the library is organised

Two taxonomies, each with one owner.

**Elements** belong to eight families — `glows`, `ghosts`, `rays`,
`streaks`, `rings`, `hoops`, `caustics`, `lens_dirt`. The same eight name
the folders under `elements/`, the top-level keys of the prompt bank, the
editor's gallery filter, and every preset element's `slot`. A `slot` is what
opens when you click an element's name: the shelf of the library holding
plausible replacements for it, which is why a one-sided `glint` used as a
streak is filed under `streaks` rather than `rays`. `flare/schema.py` holds
the list as `ELEMENT_SLOTS`, and `tests/test_library.py` fails if any of the
four places drifts from it.

**Presets** file themselves with a `category` (`Anamorphic`, `Spherical`,
`Scenario`, `Utility`) and an optional `subcategory` for genuine sibling
sets — the four coatings of one anamorphic, the wide and tele cut of one
spherical. The editor's `presets ▾` menu groups by them. The category lives
*inside* the preset rather than in its filename on purpose: a saved workflow
loads a preset by name, so renaming files to sort them would break graphs
that already exist.

## Controls drawn from real lenses

Every one of these exists because a shape kept recurring across the survey
and could not be expressed. All default to off.

- **`shift` [x, y]** — a nudge across the *screen*, applied after the flare
  axis, so it does not swing as the light moves. A companion ghost carries
  its own anamorphic streak a fixed drop below the source; `offset` cannot
  say that.
- **`pin` [x, y]** — lock one coordinate to the frame (`null` leaves an axis
  free). With y pinned to −1 an element rides the top edge directly above
  the light, which is the hotspot most anamorphics throw.
- **`shade`** (−1..1) — light the element from one side only, across its own
  axis. A ghost bright on the edge facing the source reads as a comet, and
  as a pointed crescent once the frame cuts it.
- **`curve`** (streak) — bow the line into a shallow arc. Positive sags.
- **`dash`** (streak) — break the line into seeded segments with gaps.
- **`crescent`** / **`crescent_feather`** (iris, ring, hoop, spectral) — cut
  the ghost with a disc of its own size, the way a barrel clips a
  reflection: 0 leaves it whole, 0.9 leaves a thin arc. Rotation aims it.
- **`ring`** / **`ring_width`** / **`spectral`** (orbs) — gather the specks
  onto a rim instead of filling the disc, and let each diffract its own
  colour: the dusty rim of a front-element reflection.

A one-sided ray is `glint` with `points: 1`; two of them, aimed opposite and
tinted differently, make the warm/cool two-tone streak of an old anamorphic.
Spectral caps at the ends of a streak need no new key — `dispersion` already
pushes red furthest along the line.

## Notes for compositors

- `flare_pass` is the flare rendered over black, encoded to sRGB. With
  `clamp_output` off it is unclamped HDR. Adding the decoded pass over the
  decoded source in linear light reproduces the `image` output exactly.
- `add` is the physically correct blend mode. `screen` is provided as a
  soft-clipped convenience and operates on values clamped to [0, 1].

## License

Apache-2.0. Everything is procedural and original; no proprietary preset
formats are read or written.
