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

## 4. Repo location

Repo root is `C:\WORK\Comfy_Flares\comfyui-flarecore`; the spec and planning
docs stay in `C:\WORK\Comfy_Flares\docs`.

## 5. Rotation units

Element `rotation` in presets is degrees (user-facing). Internal math is
radians.
