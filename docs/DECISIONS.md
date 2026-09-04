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

## 4. Repo location

Repo root is `C:\WORK\Comfy_Flares\comfyui-flarecore`; the spec and planning
docs stay in `C:\WORK\Comfy_Flares\docs`.

## 5. Rotation units

Element `rotation` in presets is degrees (user-facing). Internal math is
radians.
