# Lens Lab — experimental spherical optics

Lens Lab is a mode of **Flarecore · Render**, not a separate application or an
image-generation model. Existing workflows keep their node inputs and outputs.
Presets without a `lens_lab` object use the existing artistic renderer unchanged.

## Use inside ComfyUI

1. Restart ComfyUI after installing this version and refresh its browser page.
2. Open **Lens Lab** in the Flarecore Render toolbar. Alternatively load
   **Utility → Lens Lab → PBRT Wide 22 mm (experimental)** in the preset gallery.
3. The **Simple** tab opens first: choose Balanced, Cool, Warm or Uncoated,
   then adjust Reflection brightness, Light glow, Light rays and Aperture.
   Uncoated is a diagnostic starting look, not a commercial lens preset.
   Drag the preview source to explore the result without moving your tracker.
4. Use **Advanced** for grouped aperture, coating, source-finish, sensor/quality,
   surface and path controls. **Inside the lens** shows a filled glass cutaway,
   the amber aperture and green sensor. Click glass to open its inspector;
   cyan marks the selected surface. Its position and prescription stay fixed.
   Inspect a reflection path to see its ghost alone and follow the amber ray
   bounces. **Back to full look** restores the complete inspection preview.
5. Choose the footer's **Output** mode and **Apply to flare**. Final ray quality
   is under **Advanced → Sensor, exposure & quality** and is also reported in
   the footer. The small **i** beside settings explains units, effects and
   limitations on hover or keyboard focus; Escape dismisses a help bubble.
6. Run the graph with ComfyUI's normal **Run** button. Save your workflow or use
   the existing preset save action to retain the configuration.

Edits are transactional: Cancel/Escape discards them. Apply is one editor undo
step. If the underlying preset/group changes while the dialog is open, Apply
refuses to overwrite that change. The dialog mounts on the page, never inside
the node's measured content, and never assigns a node width.

Both tabs edit the same draft. Switching tabs preserves all settings, including
surface overrides, quality and excluded reflection paths. Returning to Simple
clears only temporary path isolation / ghosts-only inspection so it shows the
whole look again. Applying a starting look replaces coatings, reflection
strengths, exposure and source finishing, but preserves aperture, sensor, quality,
path exclusions and tracking. Edited looks are labelled Custom.

On wider windows Advanced places the preview beside the cutaway; narrower
windows stack them. Collapse **Inside the lens** to give the preview more room.
Simple keeps the cutaway folded by default. The diagram depicts the renderer's
reference geometry and ray paths, not a fabricated lens barrel or product image.

The node's preset button reports the active Lens Lab coating mode after Apply;
the previous artistic preset is no longer highlighted as the active library
selection. In physical-only mode, the node shows lens controls instead of
inactive artistic element rows. **Include artistic elements** makes a hybrid
look; **Use artistic stack** restores the retained elements without deleting
the lens settings. These changes support the editor's undo/redo.

While a preview is updating, its previous image is dimmed and labelled as
outdated. Apply is unavailable until the current preview has finished. If a
preview fails, retry with Refine preview or cancel; an old image is never
presented as the current result. Final ray quality, enable/hybrid switches and
the node's scene context are still outside the small inspection preview, as
described below.

The preview source is deliberately separate from your tracked source. Dragging
it does **not** change or bake tracker data. Final rendering uses the node's
manual, detected, tracked, connected or per-group source positions and visibility.

### Physical only versus hybrid

The shared **Output** selector has three choices: **Lens only** produces optical
ghosts plus a separately controlled artistic source finish; **Lens + my elements**
also adds the retained artistic stack; **Artistic stack only** disables the lens
without deleting its settings. Set Light glow and Light rays (Source glow/rays
in Advanced) to zero for a trace-only lens pass. Existing artistic elements
always stay saved. The output choice does not change the inspection preview,
which never includes that artistic stack.

Physical rendering uses global master/intensity/tint and node intensity. Global
scale, aspect, fringe, flicker and edge-fade are artistic-layer controls, not
changes to lens geometry. The flare anchor does not reposition physical ghosts;
their geometry follows the lens and sensor. Source travel damping is bypassed
while Lens Lab is enabled, including for the artistic portion of a hybrid pass.
Depth/image visibility attenuates ghost energy; it does not shrink the ghosts.

The dialog preview offers **Ghosts + source finish** and **Traced ghosts only**,
on black with a unit white source, sRGB display conversion and clipping. It
excludes the existing artistic element stack, the plate,
global tint/intensity, tracking and scene visibility. Isolating a path previews
it even if its render checkbox is off; isolation is never saved as disabling
all other paths. Final render checkboxes *are* saved. Numeric Preview source X/Y
also allow off-screen inspection; they never edit the tracker.

**Ghost exposure** changes the traced reflections only: one stop doubles them,
without scaling source finishing. To strengthen the complete look together, use
Flarecore's master/base brightness or node intensity. Raising ghost exposure
alone can make large aperture reflections dominate a photographic source.

Use linear plates and **add** blending for light addition. Existing screen
blending, output clipping and sRGB options remain available as artistic output
choices. Outputs remain `image`, `flare_pass`, and luminance `flare_alpha`.

## What is simulated

The single supplied model is PBRT 3rd edition's published `wide.22mm.dat`
prescription (Table 6.1), with an effective paraxial focal length of approximately
22.0235 mm. It is an educational reference, **not** an exact recreation of a
named commercial cinema lens. Numerical prescription source:
[PBRT: Realistic Cameras](https://pbr-book.org/3ed-2018/Camera_Models/Realistic_Cameras#LensSystemRepresentation).

The implementation is independent code; it does not copy commercial assets,
presets or renderer implementation. It uses the published radii, spacing,
refractive indices and clear apertures as numerical reference data.

- 12 refracting spherical interfaces and one polygonal/circular stop, in mm.
- 66 possible pairs of reflections. Each ray travels forward, reflects at the
  rear member of a pair, travels backward to the front member, reflects again,
  then travels forward to the sensor.
- Sphere intersections choose the vertex hemisphere. Rays refract by Snell's
  law, reflect geometrically, and are clipped by every crossed clear aperture.
- Unpolarized Fresnel reflection and transmission, with a nine-band (420–700 nm)
  single dielectric-film coating model on air/glass interfaces. Both forward
  and reverse crossings carry the spectral response. Cemented interfaces stay
  uncoated; total internal reflection remains total. No per-ghost brightness
  normalization is used. Mixed surface coating designs are authored looks,
  not measurements from a named lens.
- The sensor's infinity plane is obtained from a paraxial focus calculation;
  individual ghost paths use full spherical intersections, not paraxial ghosts.
- A deterministic scout fits a pupil grid per reflection path. Traced pupil
  triangles carry signed clear-aperture margins and linear irradiance. Exact
  polygon/pixel clipping integrates their coverage and clips the aperture
  boundary between vertices, rather than dropping complete boundary cells.
  Even subpixel footprints use exact area coverage, without a pixel-sized
  post-blur. Truly collapsed points retain their clipped input flux; singular
  lines use a numerical ribbon at most .001 output pixel wide.
- Source UV is mapped to incoming field angle using sensor dimensions and focal
  length. Off-frame UVs are supported by final renders. The reference is a distant
  point source, not a finite-distance emitter or a complete camera projection.

## Honest limitations

This is an optical prototype, not yet a production replacement for Real Lens
Flares or a calibrated radiometric camera simulation.

- Constant-index glass geometry: coating colours are spectral, but refraction
  does not yet disperse wavelengths into different trajectories.
- Surface "reflection remaining" is a neutral multiplier on the modeled
  reflection response, with complementary transmitted energy. The film is an
  ideal lossless single layer (MgF2-like index 1.38), **not** measured multilayer
  coating data. Design wavelength is not physical film thickness.
- Source core/halo/rays are an explicitly artistic, frame-stable approximation.
  There is no physical diffraction/starburst simulation, scattering, lens dirt, aspheres,
  anamorphic surfaces, moving zoom groups or direct imaging of the plate.
- Only two-reflection paths; no higher-order bounce paths or sensor reflections.
- Very small surviving paths can still challenge the scout and coarse grids.
  Caustics remain pupil-grid-sensitive: compare grid convergence as well as
  output resolution. Exact area coverage removes the old resolution-dependent
  postfilter, but does not turn a coarse pupil grid into a converged optical
  solution. No real-time or universal convergence promise is made.
- Brightness is relative to a unit incident source and adjustable exposure,
  not lux, measured camera exposure or a calibrated material database.
- Final frames allow up to **16 sources**. Cost scales with source count, frames,
  ray grid and output resolution. MPS is not verified; CPU and CUDA are tested.

Final grids: Draft 48², Standard 96², Fine 192² pupil rays per enabled path.
The browser preview is fixed at 512×288, with 48² rays (Refine: 96²). Preview
work uses ComfyUI's selected compute device in a background thread with one
active request slot (CPU fallback outside ComfyUI). It does not queue a graph
or spend image-generation credits. Final rendering uses the normal node compute
device. Evidence timings include the device and configuration; concurrent QA
jobs are not representative standalone latency measurements.

## Architecture and saved data

| File | Responsibility |
| --- | --- |
| `flare/lens_lab.py` | Prescription, validation, Snell/Fresnel tracer, ghost reconstruction and matching diagram rays |
| `flare/lens_raster.py` | Exact clipped-triangle pixel coverage and irradiance integration |
| `flare/lens_finish.py` | Spectral coating weights and separate artistic source finish |
| `flare/schema.py` | Optional `lens_lab` preset field and round-trip validation |
| `flare/engine.py` | Dispatch physical-only or hybrid passes without changing legacy behavior |
| `nodes/render.py` | Existing source/visibility/batch pipeline; physical source travel policy |
| `nodes/lens_lab_api.py` | Bounded, local-only preview/catalog routes; single background worker |
| `web/flarecore_lens_lab.js` | Shared Simple/Advanced draft, preview scheduling, surface controls and pair isolation |
| `web/flarecore_lens_diagram.js` | Filled reference-glass cutaway, renderer ray paths and keyboard-selectable surfaces |
| `web/flarecore_lens_styles.js` | Responsive two-tab layout and shared dark/amber visual language |
| `web/flarecore_ui.js` | Toolbar entry, existing undo/save/group integration |
| `web/flarecore_help.js` | Shared hover/focus help, including native-dialog placement |

`lens_lab` is optional, under each individual preset (and each group's preset).
Its own `version` is 1. Surface IDs and pair IDs are zero-based, e.g. `6:7`
is displayed as S8 ↔ S7. Settings contain model, enabled, f_stop, blades,
rotation, sensor_width, sensor_shift, exposure, quality, include_artistic,
disabled_pairs, coating_profile, coating_nm, coating_strength, source_glow,
source_rays, source_size, source_style and per-surface reflection/coating
overrides. Unknown optical
fields are rejected rather than silently ignored.

Routes: `GET /flarecore/lens_lab` and `POST /flarecore/lens_lab/preview`.
Preview input is bounded to 32 KiB, fixed image dimensions, validated source
coordinates, one source and a maximum 96² ray grid. Busy requests receive 429.
Disconnecting a client does not free the slot until its worker actually exits.

## Verification and next stages

`tests/test_lens_lab.py` covers prescription/focus, reflection ordering,
Fresnel/TIR, sphere hemispheres, polygon stops, validation, mesh energy,
CPU/CUDA parity, output buffers, hybrid behavior, batching, half-precision node
output and inactive missing textures. The local `tests/serve_lens_lab_qa.py`
harness exercises the actual designer and preview endpoints without changing a
user's running workflow. It is a development tool, not another application the
user needs to run.

`tests/test_lens_designer.mjs` exercises the real designer with an offline DOM
and controlled preview transport: shared tab values, Apply/Cancel, stale replies,
retry, output selection, starting looks, path isolation and surface overrides.
It also verifies finite cutaway geometry and keyboard selection. These are
behaviour tests, not substitutes for live browser layout checks.

Spectral colour matching uses the analytic CIE 1931 fits described by
[Wyman, Sloan and Shirley (2013)](https://jcgt.org/published/0002/02/01/paper.pdf),
with equal-energy working-RGB white balancing, not a calibrated camera response.

Next priorities: stronger scout/adaptive-sampling guarantees and temporal
convergence tests; tracer/reconstruction profiling and caching; dispersive glass
and measured multilayer coatings; physically separate aperture diffraction;
then aspheres/anamorphic
prescriptions and actual moving lens groups for zoom. Exact named-lens matches
require documented prescriptions and measured calibration, not only screenshots.

The entrance-pupil scout now increases its density with the f-number. This
prevents a narrow reflection footprint from falling between scout rays at a
stopped-down aperture; final quality still controls the fitted ray grid. The
adjacent-frame small-ghost regression is tested separately from rasterization.

### Matching a photographed source

If a plate already contains a bright core and bloom, start with **Source glow**
and **Source rays** at zero; add internal reflections without drawing a second
light over the real one. Use the node's source radius to cover the entire
luminous body, not only its brightest pixel. Radius is a fraction of image
height: 0.02 is 21.6 pixels at 1080p. An extended lamp can need much more.

Image visibility estimates relative aperture energy against a clear reference
in the clip. A surrounding ring estimates background; subtraction is bounded
by source-to-background contrast so a bright, contaminated ring cannot turn
minor flux loss into complete extinction. This conservative safeguard is not
source segmentation. Exposure changes, clipped sources and an incorrectly
sized aperture remain ambiguous; check the obstruction mode and source radius
on your own plate. Physical ghosts dim with source visibility, not scene-object
masks over their positions.

Visual evidence generators under `tests/` write raw linear arrays, fixed-display
PNGs, settings, source coordinates and renderer fingerprints. They do not assign
their own quality score. Intermediate QA reports and historical renders must not
be presented as final approval of a later candidate. Regression tests and live
ComfyUI checks are separate from visual review and measured optical validation.

The node cogwheel opens a bounded, scrollable advanced-settings section inside
the editor. Native widgets stay hidden; their values, connections and serialized
order remain intact. Opening and closing it should not change node width or
height. A connected value is read-only in this section.
