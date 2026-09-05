# Design decisions (deltas from the build spec)

Decisions made with the project owner on 2026-09-04, deviating from or
extending the element math in the original build spec. Everything else follows
the spec as written.

## 1. Dispersion sign: red outermost

The spec's `scale_R = 1 + dispersion * k` applied to local coordinates renders
the red channel *smaller* (a feature at radius r appears at r / scale), putting
blue on the outside of dispersed elements. Real diffraction coronas and lens
ghost fringing put red outermost — longer wavelengths diffract more.

**Decision:** coefficients flipped. Red samples use coordinate scale
`1 - dispersion * k`, blue `1 + dispersion * k`, with `k = 0.05`.

## 2. Optional `dispersion_samples` element key

Three-sample RGB dispersion renders a high-dispersion element as three discrete
colored copies rather than a continuous rainbow. Real dispersion is a
continuum.

**Decision:** each element accepts an optional integer `dispersion_samples`
(default 3, minimum 3). At 3, behavior and cost are exactly the spec's R/G/B
path. Higher values evaluate N coordinate scales spanning the same ±dispersion
range, weighted by a piecewise-linear wavelength→RGB ramp that is normalized
per channel so total energy matches the 3-sample case. The `spectral` element
type defaults to 7 samples and `dispersion = 1.0`.

## 3. Iris edge model kept as specced (argument considered, rejected)

The `d = r / r_edge` angular-folding form makes the smoothstep edge band
physically wider at polygon corners than at edge midpoints. A true
perpendicular-distance SDF would make it uniform. Kept the spec form: real
iris blades are curved, so aperture ghosts genuinely have softer corners; the
"artifact" is the more physical look. `edge_softness` is clamped to a small
epsilon since the smoothstep degenerates at exactly zero.

## 3b. Occlusion reference depth is explicit, not sampled

Decided 2026-09-04 after testing against a real Depth Anything V2 map.

The original implementation read the light's own depth from the pixel under
the light, then counted disk samples nearer than that. This fails in the case
that matters most: once an occluder covers the light, the sampled depth *is*
the occluder, so nothing is nearer, occlusion returns 0, and the flare renders
at full strength on top of the object hiding it. Measured on a test scene
(sun behind a foreground pillar), flare energy went 0.0672 in open sky ->
0.0427 at the pillar edge -> 0.0686 fully behind it: a fade followed by a pop
back to full, exactly the behaviour the spec set out to avoid.

**Decision:** the light's depth is now an explicit `light_depth` node input on
the same 0..1 scale as the depth map, where 0.0 means the light is at infinity
(a sun or sky light) and 1.0 puts it at the camera. A sample occludes when it
is nearer than `light_depth + margin`, with margin fixed at 0.1 to absorb
depth-map noise. Default 0.0 is correct for the dominant flare case with no
tuning, and raising it handles lights that sit mid-scene.

Verified after the change: 0.0672 -> 0.0027 -> 0.0000 across the same three
positions, with the open-sky case bit-identical to before.

Fixed thresholds were preferred over deriving a reference from a per-frame
depth percentile, because an adaptive reference drifts as scene content
changes and makes the occlusion breathe on video.

The transition ramp width is set by `occlusion_radius`: roughly 20 px at the
0.02 default on a 1024-wide frame, ~46 px at 0.08. Raise it (and/or the
adapter's `blur`) for a softer, more filmic fade on moving shots.

## 3c. Depth estimation stays out of the pack

Hard constraint 1 forbids models, inference, and network calls. Monocular
depth estimation needs a model, so `flarecore` never performs it. Depth comes
from whatever upstream node the user prefers (Depth Anything, MiDaS, Zoe,
Metric3D, or a rendered Z-pass) and enters through `FlareRender.depth`.

`FlareDepthAdapter` is the sanctioned helper: pure tensor math that normalizes
range, flips near/far convention, remaps levels, and blurs edges, so any
source behaves predictably. It estimates nothing and adds no dependencies.
Its `normalize` mode defaults to `per_batch` because per-frame normalization
rescales every frame independently, making a static object's depth drift as
other content enters the shot.

## 3d. Movable flare anchor (2026-09-04, owner-directed)

The axis anchor C is no longer pinned to frame centre: FlareRender exposes
flare_x/flare_y and the engine takes element_center = P + t*(E - P). Element
spacing therefore scales with |E - P|, giving direct manual control over the
distance between the source and the flare chain. Defaults keep the classic
behaviour (anchor = centre).

## 3e. Texture elements and the element forge (owner-directed constraint change)

The original spec banned texture-based elements. The owner requested
AI-generated custom elements, so the constraint is now: the ENGINE stays
procedural-first and model-free, but a `texture` element type samples PNGs
from the user's element library (elements/<category>/<name>.png) in linear
light through the same transform pipeline. Generation happens in the user's
own graph (the shipped element forge workflow uses their local Krea2);
FlareTexturePrepare/FlareElementSave only condition and file the result.
The pack ships only engine-rendered starter textures plus locally generated
examples — nothing sourced from third-party tools, so the original licensing
concern stays addressed.

## 3f. Front-end widgets are DOM widgets

Both the point picker and the stack editor are DOM widgets. Canvas-drawn
custom widgets (draw/mouse on the litegraph widget) get a fixed 20px height
from the current ComfyUI frontend regardless of computeSize, so they cannot
host an image-backed picker. Note for future widgets: the frontend sometimes
calls computeSize() with no width argument — height math must not depend
unguarded on it.

## 3g. Video architecture (2026-09-04)

Temporal stability lives in three cooperating places, each at its own depth:

- **flare/track.py** owns identity and motion: association, zero-phase
  position smoothing (a causal EMA trails a moving light by lag proportional
  to speed), fade ramps, and velocity coasting through detection loss.
- **FlareRender.occlusion_smooth** low-passes occlusion per track id. This
  exists because detection can pin to a halo sliver beside a thin occluder
  and snap across it in one frame; measured on the demo clip it cut the
  worst frame-to-frame flare-energy jump from 98.8% of peak to 34.1% (the
  remainder being the legitimate fade ramp itself).
- **FlareDepthAdapter.temporal_smooth** stills depth-model noise before it
  reaches occlusion at all.

Seeds are identity-stable (avalanche mix of global seed, optional element
id, and instance index) so nothing re-jitters frame to frame or when the
stack is reordered. New node inputs are only ever APPENDED to INPUT_TYPES:
widgets_values is positional, and the QA loop showed both an insert and a
front-loaded DOM widget corrupt every previously saved workflow.

## 3h. Anamorphic aspect is screen-space (2026-09-04, bugfix)

`global.aspect` originally multiplied every element's `stretch_x`, which is
applied in the element's rotated local frame — with `auto_rotate` the
"anamorphic" widening followed the light-to-anchor angle instead of the
screen's horizontal. A squeeze lens stretches the image horizontally
regardless of where the light is, so the engine now divides the screen x
coordinate by the aspect BEFORE the local rotation. Element positions are
untouched (only shapes widen) — the axis geometry is a property of the
scene, the widening a property of the lens.

## 3i. Procedural irregularity (2026-09-04, owner-approved)

A common `irregular` key (0..1) drives seeded low-order harmonic noise in the
element math — uneven ring/hoop brightness and radius drift, iris edge wobble
and shading, per-ray gain and angular wobble on glints, asymmetric glows,
brightness waver along streaks. The noise is a sum of integer harmonics of
the angle so it is seam-free and identity-seeded (same `_mix` chain as
jitter), which is what keeps it stable across a video. Textures skip it:
photographed elements are already imperfect.

## 3j. Element library consolidated to eight families (2026-09-04)

Seventeen folders (fog vs glows, discs vs iris ghosts vs orbs, four kinds of
ray, stripes vs streaks, spectral vs rings) meant the forge, the add menu and
the gallery each spoke a different taxonomy. The library is now glows,
ghosts, rays, streaks, rings, hoops, caustics, lens_dirt — the same eight
names in the prompt bank, `CATEGORY_OF`, and `elements/`. Legacy folder names
are remapped in both the loader and the editor so old presets keep loading.

## 3k. Rule-based animation and lens behaviour (2026-09-05)

Owner shared tutorial transcripts of a commercial flare plug-in as
inspiration; the ideas below are original implementations of behaviours
real lenses (and that tool) exhibit. None of its code, formats or names are
referenced.

- **Translation locks** (`move: [mx, my]`): an element follows only that
  fraction of the light-driven motion per screen axis, with the anchor as
  its rest position. `[1, 0]` gives the horizontal-only ghost bars of an
  anamorphic flare.
- **Triggers** (`trigger` block): brightness (additive, so an element can
  sit at 0 and exist only while its rule fires), scale (multiplicative) and
  colour driven by the distance of the light or of the element itself to the
  frame border or centre, with linear/smooth/exponential ramps. Animation
  without keyframes; deterministic per frame.
- **Flicker** (`global.flicker_amount/speed`): per-light seeded harmonic
  noise over the frame index; tracked lights carry their track id as the
  flicker index so each lamp pulses on its own.
- **Edge fade** (`global.edge_fade_start/range`): a lens-hood — lights that
  travel beyond the frame edge fade out over a set distance.
- **Chromatic fringe** (`global.fringe`): a post-op on the finished frame,
  red magnified outward and blue inward about the frame centre.
- **Circular completion** (`params.completion/completion_feather` on ring,
  hoop, glint, spectral): a feathered angular window centred on local +u.
- **Orbs** element type: seeded out-of-focus discs/polygons on the lens
  (screen_space by default) each lit by 1 / (1 + (d / illumination)^2) of its
  distance to the light; the engine hands the light's position in element-
  local coordinates as `params["_light_local"]`.
- **Solo** element key: while any element is soloed only soloed elements
  render (non-destructive isolation while tuning).
- **Sub-pixel anti-aliasing**: the engine passes one pixel's size in local
  units (`params["_px"]`); ring/streak/glint thickness below 0.8 px is widened
  to that with gain reduced to conserve energy. A 0.002-thick ray at 1080p
  used to alias and shimmer frame to frame; now its energy is the same
  fraction of the frame at 108 px and 864 px (tested).
- **Scene-sampled light colour** (`FlareRender.scene_color`): luminance-
  weighted chromaticity of the plate around each light, normalised to unit
  luminance and blended toward neutral, multiplied into that light's flare.

## 3l. Nodes render on ComfyUI's compute device (2026-09-05)

The engine follows the device of the tensors it is handed (spec constraint).
ComfyUI, however, hands every IMAGE to a node on the CPU regardless of where
it was produced, so "follow the input" at the NODE level pinned the whole
renderer to the CPU: 2.3 s per 540p frame on a machine whose GPU does a 1080p
frame in 60-90 ms. FlareRender now asks `comfy.model_management` for the
compute device (lazily — importing it touches the CUDA runtime, which must
not happen at module load or in headless tests), renders there, and returns
results on the input's device. Outside ComfyUI nothing changes. Measured:
253 ms per 1080p frame through the node including PNG encoding, from 2.3 s.

## 3m. Dynamic triggering completed (2026-09-05)

Owner pointed at the transcript's triggering section (01 @ 00:20:45) as the
reference for what the feature should cover. Three pieces were missing from
3k and are now in:

- **`mode: "light"`** - the rule fires on the distance between the ELEMENT
  and the light, not on frame geometry. It always measures the element (the
  `source` choice is ignored and hidden in the editor): with source "light"
  the distance would be identically zero, which is a degenerate config, and
  "how close is this ghost to the source" is the only useful reading. It is
  therefore evaluated per count-instance, so one chain lights up only on the
  instances that pass near the light.
- **`rotation`** as a triggered property (degrees added at full trigger).
  The element's cos/sin are hoisted out of the instance loop for speed, so a
  rotating trigger recomputes them per instance and every downstream use
  (including the orbs light-local transform) reads the per-instance frame.
- **Trigger preview** - the editor paints the trigger region over the point
  picker in red, live while inner/outer/mode change, one rule at a time
  (arming another or collapsing the row clears it). Rows whose element has a
  rule carry a red left edge so a preset's rule-driven elements are visible
  without opening anything.

The preview needs the field at ~10k points per repaint, so it is a
JavaScript port of `trigger_factor` rather than a server round-trip per
slider event. The duplication is real; `test_trigger_factor_reference_values`
pins eight (mode, falloff, point) -> value cases, and both implementations
carry a comment pointing at the other. Verified in the browser by sampling
the painted canvas against those values across the frame.

## 3n. Trigger lab workflow (2026-09-05)

The video lab needs footage with a trackable light. `flarecore_trigger_lab`
demonstrates rule-based animation with no assets at all: an EmptyImage batch,
FlareKeyframes sweeping the light across and out of frame, and the
Trigger Showcase preset whose three rule elements are named for the mode
they use. 64 frames at 960x540 render in ~6 s. The video lab now bakes its
preset into the widget instead of taking it from a FlarePresetLoader link,
because a linked preset_json leaves the editor with nothing to edit.

## 3o. Choosing what drives the light (2026-09-05)

Owner reported the video lab ignoring the picker. Measured: with the `lights`
input connected, `light_x`/`light_y` are overridden completely (max pixel
delta 0.0000 between light_x 0.2 and 0.8), while `flare_x`/`flare_y` still
place the anchor (delta 0.78) because FlareTrack emits no per-frame anchor.
So half the picker was live and half was dead, with nothing saying which.

Two fixes:

- **FlareLightsSwitch** node: one `source` combo picking between a connected
  tracker, connected keyframes, and "manual", which returns `None` so
  FlareRender falls back to its own position_mode and picker points. Both
  sources stay wired, so switching approach is a dropdown rather than a
  rewire. Verified through a live graph that a None FLARE_LIGHTS output
  passes down a connected link without error and hands the light back.
- **The picker says what actually placed the light.** FlareRender reports the
  source it used in its ui payload (`fc_light_src`), and the editor greys the
  light handle and captions it when something else owns it. Reporting the
  runtime source beats inspecting links in the frontend: with the switch on
  "manual" the link is still connected but the light is the picker's again,
  and only the node knows that.

Keyframing both points already existed (FlareKeyframes `anchor_keys`); it was
simply not wired into any shipped workflow. The video lab now carries Track,
Keyframes and the Switch together.

## 3p. detect_max_lights caps flares, not detections (2026-09-05)

Owner reported the tracker jumping between sides of frame on a drive down an
avenue of trees. Reproduced with two dappled patches of near-equal brightness
trading places: the per-frame argmax hops, and because `detect_max_lights`
was passed to the DETECTOR, the tracker received exactly one candidate per
frame. Every candidate that could not continue an existing track opened a new
one, uncapped, so the clip ended up with two (then three) permanent tracks
alternately brightening — read on screen as one flare teleporting.

Two changes:

- **Detect a pool, keep N tracks.** The node now detects
  `max(detect_max_lights * 8, 12)` candidates and passes
  `max_tracks=detect_max_lights` to the tracker. Association already prefers
  the NEAREST candidate, so spares are exactly what keeps the followed light
  fed through a frame some rival blob wins. `detect_max_lights` now means
  what its name says: how many flares may exist.
- **The birth cap counts every live track, not just matched ones.** Counting
  matched tracks let a rival be born beside a track that was merely holding
  or fading — the same two-flares-taking-turns failure in slower motion. A
  light that reappears near a fading track re-matches and revives it.

Measured on a synthetic avenue-of-trees clip (a sun plus nine flickering
canopy gaps): lights per frame went from [1,2,2,3,2,2,3,3,3,3,3,3] to all 1s.

Capping tracks introduces one new failure mode — the tracker locks onto
whatever won frame 0, and if that is the wrong light there is no escape — so
FlareTrack also gained an optional **search region** (`search_radius`,
`search_u`, `search_v`, appended last; radius 0 = whole frame). Candidates
outside it are dropped before tracking, the overlay draws a dashed cyan ring
showing where the tracker may look, and the report says how many candidates
the region rejected. Distances are in units of frame height, matching
`max_jump`.

## 3q. max_jump is a gate, and its default was far too loose (2026-09-05)

Follow-up to 3p: with the flare no longer duplicating, the remaining
complaint was the light wandering around the sun. Reproduced with five
similar-sized blown-out blobs clustered within ~0.15 of frame height, taking
turns being brightest (what a canopy does to a sun): the tracked point roamed
the cluster, worst frame-to-frame move 0.048, spread 0.078.

Two causes, one of them the real one:

- **Association anchored on the smoothed state.** The EMA position sits
  BETWEEN rival blobs, so "nearest candidate" flips between them. It now
  measures from the track's predicted position — the last actual observation
  carried forward by a velocity computed from observations rather than from
  the damped state. Fixing this alone only moved 0.055 to 0.047.
- **`max_jump` was sized like a tolerance, not like a gate.** It is the only
  thing deciding whether a candidate may continue a track, so at the old
  default of 0.12 (0.15 in the shipped workflow) every rival within 12% of
  frame height was a legal match. Sweeping it showed a cliff: 0.15 -> 0.048
  worst move, 0.10 -> 0.042, and at 0.06 and below 0.0003, locked for the
  whole clip. Default is now 0.06 (0.05 in the video lab).

A light in real footage moves well under 0.03 of frame height per frame, so
the tighter gate costs nothing — and because association measures from the
PREDICTION, a genuinely fast light keeps its track once moving; a test pins a
source crossing at 0.055 of frame width per frame staying a single track.

The detector now also reports `energy` (above-floor luminance summed over the
peak's neighbourhood) and association penalises a candidate carrying much
less of it than the light being followed. Brightness cannot discriminate in
this footage because every bright thing has clipped to pure white; size
still can. It is a secondary effect next to the gate, kept because it is the
part that generalises to a sun beside a small specular glint.

## 3r. One node for the video path (2026-09-05)

Owner: "I feel I have too many nodes (detect the light, flare depth adapter,
pick which one drives the flare), I only want one node." Tracking, depth
conditioning and light-source selection folded into FlareRender:

- `position_mode` gained `track`, `track_dots` and `path`. Extending a COMBO
  is safe for saved workflows because widgets_values stores the selected
  STRING, and every previous option still exists.
- Depth conditioning happens in the node when a raw map is wired to `depth`.
  **The appended inputs default to the previous behaviour** — `depth_normalize`
  is `as_is`, blur and temporal smooth are 0. The first cut defaulted to
  per_batch normalisation and broke three depth tests, correctly: normalising
  rescales the map, and `light_depth` is an absolute reference ON that scale,
  so a constant-depth wall became a no-op. Normalisation can only ever be
  opt-in.
- FlareTrack, FlareDepthAdapter and FlareLightsSwitch still exist and still
  work; they are no longer required, and the shipped video lab went from 11
  nodes to 6, one of which is ours.

## 3s. Dot-matte tracking and drawn paths (2026-09-05)

`track_dots` is for a control layer: white dots on black, one flare per dot.
The threshold is taken relative to the brightest thing in the CLIP rather
than as an absolute, because a matte is whatever white the render produced —
the owner's example peaks at 0.92 sRGB over a lifted black, which an absolute
0.8 linear threshold misses entirely. Each dot becomes its own track with its
own identity, so `detect_max_lights` is simply how many dots to expect.

Measured on that clip (174 frames, 1-3 dots at a time): 98.6% of dots carry a
flare within 0.03 of their centre, median distance 0.0022. The flares that
sit on no dot are fade-out tails after a dot leaves, which hold/fade control.

A note on measuring: the first evaluation of this looked terrible (mean error
0.077) because the ground truth averaged every bright pixel into one
centroid, which lands between dots when several are present. The tracker was
right and the metric was wrong.

`path` mode follows a route drawn on the picker: press `path` at its top
right, click to drop points, drag to move, shift-click to remove. The light
travels the whole path across the batch, so spacing points controls speed.
The curve is Catmull-Rom with duplicated end points so it passes through
every point; `sample_path` in flare/track.py evaluates it and the editor
draws the same curve in JavaScript, with `test_path_reference_points` pinning
values both must produce.

## 3t. The light source is a mode, and the UI follows it (2026-09-05)

Owner: "I want position mode as a drop down in UI, and options appear in UI
node when I chose an option. So if I chose path, I can draw path otherwise
I'm not able to."

`position_mode` now sits in a dropdown at the top of the editor panel rather
than behind the gear, and the row below it shows only the controls that mode
uses: threshold and count for detect, plus smoothing and max jump for the
track modes (relabelled "dot threshold" and "max dots" under track_dots), a
point count and a clear button for path, and nothing but a sentence for
manual. The controls bind to the NODE's widgets, not to the preset.

The picker is gated on the same value instead of carrying its own toggle: a
click only edits the path in path mode, and the light handle greys out in
every mode except manual — read from the widget rather than from the last
render, so it is right before anything has been queued. The anchor keeps
working everywhere, including path mode, where a click near it drags it
instead of dropping a point.

One bug worth recording: reading the mode in the light-handle block while
declaring it further down in draw() threw "Cannot access 'posMode' before
initialization" on every repaint, which silently stopped the panel
rebuilding — the dropdown changed the widget but the row never updated. The
console was what found it; the symptom looked like a rebuild bug.

## 3u. Lens-surface plates fill the frame (2026-09-05)

Owner: dirt, orbs and droplets "must be rendered in 16:9 and cover the whole
frame, no feather, visible only where the light is with controlled fall off."

A plate that lives ON the front element is not a subject on a black field, so
the square-crop/centre/feather pipeline is wrong for it in every particular —
the feather alone paints a dark vignette across the frame it is meant to fill.

- `FlareTexturePrepare` gained `frame`: `wide_16_9` skips the square crop,
  the energy centring and the feather, and resizes to 16:9 (2048x1152). The
  lens_dirt library was re-forged at 16:9 through it; the category is now
  uniformly full-frame.
- New element key `fill_frame`: the element's local coordinates span the
  frame exactly at scale 1, at the FOOTAGE's aspect rather than the file's,
  and the anamorphic squeeze does not apply to it — the plate is on the lens,
  not seen through it. Verified on a 2.39:1 render with a 16:9 plate.
- `mask_falloff` on FlareRender replaces the hard-coded 0.35: how far a
  light's glow reaches when it reveals light_mask elements. Measured on the
  demo, flare energy goes 1251 / 2566 / 4330 at 0.15 / 0.35 / 0.8.

Where the plate SHOWS is decided at render time by `light_mask` and this
falloff, never by baking a shape into the file.

## 3v. Everything but the panels hides behind the gear (2026-09-05)

Owner found track_smoothing, track_max_jump and the rest sitting on the node
face. They were: the hide list named the widgets to hide, so every input
appended since (scene_color, the track and depth controls, light_path,
mask_falloff) stayed visible until someone remembered to add it. Inverted to
name what STAYS — the two DOM panels — so anything appended later is hidden
automatically.

Fixing it introduced a duplicate `const PANEL_WIDGETS`, which is a module
evaluation error: the extension silently did not register at all, and the
node fell back to bare widgets with no picker or editor. `node --check`
parses as a script and did NOT catch it. The reliable check is to strip the
imports and parse as a module:

    sed 's|^import .*|//|' web/flarecore_ui.js > mod.mjs && node --check mod.mjs

which does catch a duplicate declaration. Console errors alone were
misleading here — the browser had cached the previous module, so old stack
traces kept surfacing after the source was already fixed.

## 3w. The studio: one workflow, three benches, one switch (2026-09-05)

Owner wanted the element forge, flare lab and video lab in one workflow with
a toggle where activating one disables the other two, plus a one-node forge
UI: category -> element dropdowns, the bank prompt exposed for editing, and
an extra-style suggestion behind a toggle.

- **Forge panel** on FlareElementPrompts: a DOM panel that writes the node's
  real (hidden) widgets. Picking an element fills the prompt textarea from
  the bank and mirrors it into custom_prompt, so what is on screen is
  byte-for-byte what queues; edits flow straight through. The extra-style
  toggle writes extra_style or clears it. The bank arrives over a new
  /flarecore/prompt_bank route. No schema change: the panel drives the
  widget contract that already existed.
- **Flarecore Studio switch**: a frontend-only LGraphNode (isVirtualNode, so
  it never reaches the API) with three radio buttons. It finds the three
  groups by title, mutes every node inside the inactive two (mode 2) and
  dims their colour, and re-applies its serialized choice on load. The
  FLARE LAB group was enlarged to envelope the depth sub-bench — nodes are
  muted by group membership, and an un-enveloped depth chain would have kept
  running against a muted image loader.
- **flarecore_studio.json** replaces the three individual workflows (the
  trigger lab demo stays). The Builder gained an origin offset (`b.at`) so
  each bench keeps its own coordinates and is placed as a unit; the three
  bands share a top edge and a 1320-unit height, 200-unit gutters, with the
  switch and a title note as a column on the left.

Two frontend lessons paid for here: the current ComfyUI frontend requires a
REAL `LiteGraph.LGraphNode` subclass for registerNodeType — a plain class no
longer gets the prototype grafted on, and the symptom is a half-built node
plus unrelated-looking errors from other packs that touch every node type.
And the builder writes virtual nodes by hand (like Note), since they are
absent from /object_info.

## 3x. Studio fixes: hidden widgets, a zoom-proof switch, subgraphs (2026-09-05)

Four defects reported against the first studio build, and each had a
different root cause worth recording.

**Suppressed widgets were still painted.** The pack hid widgets by assigning
`type = "hidden"` and a zero computeSize. Measured on a stock KSampler in
this frontend: `widget.hidden = true` shrinks the node (262 -> 238), while
`type = "hidden"` does nothing (262 -> 262). The legacy idiom is inert, so
the raw `element` combo and the render node's inputs kept drawing over the
panels. `hideWidget` now sets the flag (and keeps the type assignment for
older frontends).

**The switch did nothing.** Its buttons lived in a DOM widget, and ComfyUI
hides DOM widgets below ~50% zoom — precisely the zoom you need to see three
benches at once. It is now three NATIVE litegraph button widgets, drawn on
the canvas at any scale, marked with a filled/hollow bullet.

**Nothing was muted on load.** `group.recomputeInsideNodes()` walks each
node's cached bounding box, which is only populated once the canvas has
drawn; on a freshly loaded workflow it reported empty groups, so every bench
came up live. Membership is now computed directly — the node's centre
against the group rectangle — which needs no cache and is correct on the
first pass.

**Too many nodes in the forge.** The eight-node model/sampling chain is now
one `Krea2 generator` subgraph exposing `text` and `seed`. The subgraph was
produced by the frontend's own `LGraph.prototype.convertToSubgraph` and the
result exported with `graph.serialize()`, rather than hand-authoring the
`definitions.subgraphs` format. Note the instance method is monkey-patched by
another installed pack, so the call has to go through the prototype.

Regenerating the studio is therefore two steps: `build_workflows.py` writes
the flat base, then the conversion is replayed in the browser and the export
overwrites the shipped file. The procedure is in the builder's studio
section. The flare bench also lost its depth chain — depth occlusion needs a
depth model and belongs with footage, so it lives in the video bench, and
FlareRender's `depth` input is still there for anyone who wants it.

## 3y. The owner's arrangement is the shipped default (2026-09-05)

The studio template now ships the layout the owner arranged and saved, taken
verbatim from their `user/default/workflows/flarecore_studio.json`: their
positions, their group bounds, their resized Flare Render (1075x1145) and
prompt node. Measured first that loading a template reproduces its saved
geometry exactly (zero drift across 22 nodes), so nothing in the extension
was fighting the layout — the file simply had not been updated.

One substitution: they had driven the benches with three third-party
`PixaromaGroupSwitch` nodes, reached for while the built-in switch was
broken. Those are replaced in place by three `FlarecoreStudioSwitch` nodes at
the same coordinates and title. A shipped template must not require another
pack to be installed, and the built-in one now works. The switch gained
cross-instance sync for exactly this arrangement: a copy sits above each
bench, whichever is clicked re-marks all of them, and only the clicked one
mutes the graph. (If the owner prefers the third-party node's look, swapping
it back is a per-user change, not a shipped dependency.)

Third-party bookkeeping (`ue_properties`, `ue_links`, `anomalous_hashes`) is
stripped from the file, and the benches ship muted except the flare lab.

## 3z. The forge panel is fluid, and a flex trap (2026-09-05)

The prompt panel now takes whatever node height is left below it and hands
the slack to the prompt box: node 359 -> 208px of prompt, 500 -> 349, 800 ->
649. Two details were needed.

`fitForge` cannot rely on the widget's laid-out `y`: that is only filled in
once the canvas has drawn, so on load and on a resize in an undrawn tab the
panel kept a stale height. Every other widget on this node is hidden, so the
offset is just the title bar and a constant fallback is correct.

The subtler one: `.fcore-hint` carries `flex-basis: 100%` so it wraps onto
its own line inside the horizontal light-source row. The forge panel is a
COLUMN flex container, where that same declaration means 100% of the HEIGHT —
the hint was silently claiming 694 of 854 pixels and the prompt box stayed at
its 70px minimum. Scoped to `.fcore-forge .fcore-hint { flex: 0 0 auto; }`.
Worth remembering: a shared utility class with a flex-basis is direction-
dependent, and reads completely differently in a row and in a column.

## 3aa. DOM widget height comes from getMinHeight, not computeSize (2026-09-05)

The forge panel was still fixed after 3z. Measured on the live widget: it
exposes `computeLayoutSize()`, which returned `{minHeight: 150}` straight
from `options.getMinHeight`, while `widget.computeSize(400)` reported 754 and
was ignored. The layout engine in this frontend reads the option; the
computeSize override is dead code for DOM widgets, exactly as `widget.type =
"hidden"` was dead for hiding (3x).

`getMinHeight` is a callback, so it can be dynamic. Both panels now report
their fluid height there — the forge panel `node._fcForgeH`, the stack editor
`max(280, node._fcEditorH)`. Since that value is the node height minus the
panel's own offset, it can never drive the node larger than it already is.
Verified: layout min-height tracks 354 / 654 / 954 as the node goes 400 /
700 / 1000, and the prompt box goes from 70px to 369px at the shipped node
size and 649px at 800.

The prompt box also carries `resize: vertical` so it can be dragged on its
own, and the shipped prompt node is 460x520 rather than 431x359.

## 3ab. Auto lens-plate framing, scroll retention, gallery naming (2026-09-05)

Owner forged a lens_dirt element and got a square 2K with margins: the 16:9
treatment from 3u was a manual switch on FlareTexturePrepare, and a default
that needs remembering is a default that fails. `frame` gained `auto` (the
default for new nodes; saved workflows keep their stored value) and an
optional `category` input, wired from the prompt node in the shipped studio.
auto resolves lens_dirt to wide_16_9 and everything else to square. Explicit
square/wide_16_9 still win.

Editing a row scrolled the stack back to the top: every edit rebuilds the
list. The offset is now carried across the rebuild — assigned AFTER the new
list is attached, because a detached element has no scroll height and the
first attempt silently clamped to 0.

Owner read "veil opens the glows library" as the library being out of sync.
It is in sync (veil is a glow; glows holds veiling_fog and hazy_veil), but
the gallery header named only the family. It now names the row: "veil —
pick a glows element".

The extra-style field is a multi-line box (52px minimum, drag-resizable).

## QA loop: fuzz battery, linear colorspace, chunked rendering

A 19-case fuzz battery (empty presets, 8x8 frames, fp16, lights off-frame,
uniform depth, 1x1 textures, INT-max seeds) found one real bug: `solo` on a
DISABLED element leaked every other element, because the solo scan only
looked at enabled elements. The scan now covers all of `preset["elements"]`
-- soloing a muted row means "show me only this", and only this is nothing.

`colorspace` ("srgb"/"linear") was added to FlareRender -- appended LAST,
because the workflow-alignment tests caught a mid-list insertion breaking
positional widgets_values. linear is a true pass-through for scene-linear
plates (EXR, render passes): no decode, no encode, and the pinned contract
is `out == plate + flare_pass` in linear light, so a Nuke/Resolve round
trip stays correct.

Rendering was memory-bound, not compute-bound: the working set is ~10x the
frame stack, so 96 frames of 1080p peaked at 24.5 GB of VRAM and 300 frames
was arithmetic fiction (~75 GB). The render now streams in chunks
(`chunk_frames`, 0 = auto-sized from free VRAM): detection, tracking prep,
depth conditioning, scene sampling, render, and composite all run per
chunk, with outputs accumulated on the input's home device and
`render_batch(frame_offset=...)` keeping the flicker phase continuous
across chunk seams. Measured on the same clip: 96x1080p went 24.5 GB /
25.8 s to 13.5 GB / 2.3 s (the unchunked run was spilling into shared
memory), and 300x1080p completes in 16 s. Chunked and unchunked outputs
match to one float ulp (the equality test allows 1e-6 for conv reduction
order).

## Subgraph definitions freeze the mode their nodes had

Activating ELEMENT FORGE gave a bench that looked live but failed with
"Required input is missing: image" on FlareTexturePrepare. The switch was
doing its job -- every forge node, the subgraph instance included, went to
mode 0 -- but all eight nodes INSIDE the Krea2 generator sat at mode 4.

Converting a selection into a subgraph copies each node's current mode into
the definition, and that copy is permanent. Muting reaches the subgraph
INSTANCE in the parent graph; it never reaches the nodes inside it. Both
times this subgraph was built it was built out of a muted bench -- the
natural way to build one here -- so the definition shipped dead, twice
(mode 2 in the first cut, mode 4 after the owner's arrangement).

The definition now ships live nodes, guarded by a test over every shipped
workflow. The switch also revives a subgraph whose nodes are ALL disabled
when it activates a bench: that state is never a deliberate setup (it is
just an expensive way to mute the instance), whereas SOME nodes bypassed
is a real choice and is left alone.

## A 16:9 plate is generated wide, not squashed into shape

Forging lens dirt produced a stretched image. Two things were wrong and only
one of them was visible.

The visible one: prepare's wide_16_9 branch called resize_to, a straight
non-uniform resize. The generator makes 1328x1328, the plate is 2048x1152,
so every feature came out stretched by exactly 16/9 -- droplets into
ellipses, scratches skewed. wide_16_9 now covers instead: scale by the
LARGER of the two ratios and centre-crop the overflow, so shapes survive and
the frame is still filled edge to edge. Letterboxing was never an option --
a plate with margins is not a plate.

The one underneath: cover-cropping a square generation throws away 44% of
what the sampler just made. A plate should be GENERATED at the shape it
will be used at, so FlareElementPrompts gained gen_width/gen_height outputs
(appended -- existing links keep their slots) that read the same category
map as frame: auto. lens_dirt generates 1536x864 (exactly 16:9, both sides
a multiple of 16); everything else stays 1328x1328. The studio wires them
into the generator's latent. Unwired, nothing changes -- the cover-crop
still protects any square source that reaches prepare.

## 4. Repo location

Repo root is `C:\WORK\Comfy_Flares\comfyui-flarecore`; the spec and planning
docs stay in `C:\WORK\Comfy_Flares\docs`.

## 5. Rotation units

Element `rotation` in presets is degrees (user-facing). Internal math is
radians.
