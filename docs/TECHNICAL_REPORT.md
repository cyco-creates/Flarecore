# FlareCore — Technical Report & Handoff

A self-contained description of what this project is, what it does, how it is
built, and how to change it safely. Written so that a developer or an LLM
with no prior context can pick it up and improve it. Current as of the
0.1.0 alpha, revised 2026-09-07 after the realism collection upgrade.

Revision scope: iris roundness, the one-sided ray fix, promoted calculation
precision, nine additional library textures and three Realism Studies presets.
See `REALISM_UPGRADE.md` for reproduction, asset provenance and limitations.
The subsequent adaptive-optics revision is described in
`FLARE_ANATOMY_STUDY.md` and `OPTICAL_RESPONSE.md`: independent motion curves,
an editor with draggable points, optional all-light lens illumination and
four additional adaptive presets. Current verification: 624 passing tests.
Line counts in the historical module tables below are approximate baseline
figures; they are navigation aids, not a maintained API contract.

---

## 1. What it is

**FlareCore** (`comfyui-flarecore`) is a custom-node pack for
[ComfyUI](https://github.com/comfyanonymous/ComfyUI) that renders
**procedural, physically-motivated cinema lens flares** over images and
video, plus an **element forge** for generating new flare textures with an
image model.

The defining constraint: **the flare engine is pure PyTorch math on a
coordinate grid.** No OpenGL, no shaders, no neural models, no network
calls, no file I/O inside the engine. A flare is built by evaluating
parametric functions (glows, iris ghosts, anamorphic streaks, rings,
starbursts, spectral rings, procedural lens orbs) on a normalized `(x, y)`
grid and accumulating linear-light RGB. It runs headless on CUDA, MPS or
CPU and is fully batch-native, so a video is just an image batch.

Scale: ~8,400 lines (≈5,400 Python, ≈2,900 JavaScript), 8 ComfyUI nodes,
9 element types, 9 light-tracking modes, 41 shipped presets, 218 element
textures across 8 families. The earlier alpha baseline reported 549 tests;
the revised validation result is recorded in `REALISM_UPGRADE.md`.

### What it produces

`FlareRender` takes an `IMAGE` (and optionally a depth map and per-frame
light positions) plus a JSON **preset** describing a stack of flare
elements, and outputs three tensors:

- `image` — the flare composited over the input (sRGB).
- `flare_pass` — the flare rendered over black (for external compositing in
  Nuke/Resolve/After Effects; unclamped HDR when `clamp_output` is off).
- `flare_alpha` — a luminance mask of the flare.

With `blend_mode=add` and `clamp_output=false`, adding the decoded
`flare_pass` over the decoded source reproduces `image` within floating-point
tolerance. For `colorspace=linear`, add the outputs directly without an sRGB
decode. Screen blending and output clipping do not satisfy this additive
identity. Half-precision outputs introduce final quantization.

`flare_alpha` is a convenience mask computed from the returned pass's channel
values and clamped to [0,1]; in sRGB mode those values are encoded. It is not
optical opacity, and should not be used to premultiply the additive pass.

---

## 2. Design constraints (the load-bearing rules)

These are the invariants the whole codebase is organized around. Breaking
one silently corrupts output or breaks saved workflows.

1. **The engine (`flare/`) is model-free and I/O-free.** Its external numerical
   dependency is PyTorch; helpers also use Python standard-library modules
   including `math`, `zlib`, `copy`, `json`, and `warnings`. Depth estimation, image decoding and model
   inference all live at the node layer (`nodes/`) or upstream. This keeps
   the engine testable, portable and deterministic.

2. **Everything is linear-light HDR internally.** sRGB is decoded on input
   and encoded on output at the node boundary only. Intensities may exceed
   1.0; clamping is optional and applied only at the final encode. `add` is
   the physically correct blend; `screen` is a clamped convenience.

3. **Determinism via identity-stable seeding.** An element's random jitter
   is seeded from its `id` (or its stack index if unnamed), avalanche-mixed
   (splitmix64) with the global seed and the per-instance index. Reordering
   unrelated elements does not re-jitter a tuned element; each count-instance
   of a chain gets its own jitter; adjacent seeds don't correlate. This is
   what keeps a flare stable across video frames.

4. **Node inputs are APPEND-ONLY.** ComfyUI serializes a node's widget
   values as a *positional* array (`widgets_values`). Inserting a widget in
   the middle shifts every saved value after it, silently corrupting every
   existing workflow. New widgets are therefore always appended to the end
   of `INPUT_TYPES`. Tests assert widget order. (ComfyUI also inserts
   `control_after_generate` right after any `seed` widget — account for it
   when reading `widgets_values` positionally.)

5. **DOM widgets, not canvas widgets.** The custom editor UI uses ComfyUI
   DOM widgets. Canvas-drawn litegraph widgets get a fixed ~20px height in
   the current frontend. (See §10 for the frontend gotcha list.)

6. **Depth resolution is independent of image resolution.** Occlusion
   samples the depth map in normalized `u,v`, so a depth map never has to
   match the image's H/W (Depth Anything returns e.g. 512×910 for a 720×1280
   clip). Buffers are shaped from the depth tensor, never the image.

---

## 3. Architecture: three layers

```
web/flarecore_ui.js      ← ComfyUI frontend extension (editor, picker, forge panel, studio switch)
        │ HTTP (/flarecore/*) + widget values (preset_json etc.)
        ▼
nodes/                   ← ComfyUI node wrappers: tensors in/out, sRGB<->linear, device, I/O, HTTP routes
        │ pure function calls
        ▼
flare/                   ← the engine: pure PyTorch, no ComfyUI, no models, no I/O
```

- **`flare/`** is the engine. It has no knowledge of ComfyUI and can be
  imported and tested standalone. All math lives here.
- **`nodes/`** wraps the engine as ComfyUI nodes: it moves tensors to the
  compute device, decodes/encodes sRGB, loads presets and textures from
  disk, runs the tracking/depth pipelines, and serves the frontend's HTTP
  routes.
- **`web/flarecore_ui.js`** is the main frontend extension: the
  point-picker, the stack editor, the forge prompt panel, the studio bench
  switch, and the generator switch. It reads and writes the node's
  `preset_json` string widget and calls same-origin `/flarecore/*` routes.
  `web/flarecore_motion.js` supplies the Optical Response editor and curve
  preview math. `flare/motion.py` owns response evaluation and the range
  contract served at `/flarecore/motion_schema`.

### Package loading (`__init__.py`)

`__init__.py` has two import contexts, detected **explicitly via
`__package__`** (never by catching `ImportError`, which would swallow a
missing torch and recurse). Under ComfyUI it loads as a real package; in
tests it re-execs itself once under a synthetic package name so relative
imports resolve. `tests/conftest.py::load_package()` is the sanctioned way
tests import the node layer.

---

## 4. Module map

### `flare/` — the engine

| File | Lines | Responsibility |
|---|---|---|
| `schema.py` | 537 | Preset validation, defaults, versioning. Owns `ELEMENT_SLOTS`, `PRESET_CATEGORIES`, `ELEMENT_TYPES`, all range bounds. The single source of truth for the preset contract. |
| `engine.py` | 572 | Stack evaluation and compositing. `render_stack` (one frame), `render_batch` (a clip), `composite`. Dispersion, triggers, flicker, edge-fade, chromatic fringe, light-mask/floor, the `shift`/`pin`/`shade` transforms. |
| `elements.py` | 440 | The 9 element field functions: `glow, iris, streak, ring, hoop, glint, spectral, texture, orbs`. Each takes local `(u,v)` + params, returns an intensity field. Also `_crescent_mask`, `_harmonic_noise`, `_aa_thickness`. |
| `grid.py` | 32 | Coordinate grid construction and `uv_to_grid`. |
| `axis.py` | 33 | Flare axis angle and element-center placement along `light→anchor`. |
| `colorspace.py` | 35 | sRGB<->linear, Rec.709 luminance. |
| `detect.py` | 195 | Luminance peak/region detection for auto light placement. Region-centroid (for clipped skies) and mean-shift centroid. |
| `occlude.py` | 103 | Depth-based occlusion: fraction of a sample disk nearer than the light's `light_depth`. |
| `depth.py` | 132 | Depth conditioning: normalize, invert, blur, temporal smooth. Also the separable gaussian blur reused by the engine. |
| `track.py` | 555 | Per-frame → track association (`track_lights`), whole-clip Viterbi solve (`solve_light_path`), drawn-path sampling (`sample_path`, Catmull-Rom), zero-phase smoothing. |
| `feature_track.py` | 397 | Point tracker: pyramidal Lucas-Kanade + forward-backward check + NCC re-acquire + drift correction. |
| `scene_motion.py` | 313 | Camera-motion estimation (Shi-Tomasi corners + pyramidal LK + rigid fit) for `follow` mode and scene anchoring. |
| `texture_prep.py` | 187 | Turns a generated image into a compositing-safe element: black-floor, autocenter, feather, margin, square/wide framing. |

### `nodes/` — the ComfyUI layer

| File | Lines | Nodes / role |
|---|---|---|
| `render.py` | 950 | **FlareRender** — the main node. Resolves light positions (all 9 modes), conditions depth, streams the render in VRAM-sized chunks, composites. The single biggest and most complex file. |
| `elements_lab.py` | 282 | **FlareElementPrompts** (prompt bank + style tails), **FlareTexturePrepare** (wraps `texture_prep`), **FlareElementSave** (files textures in the library), **FlareGeneratorSelect** (the lazy 2-generator switch). |
| `library.py` | 128 | Element texture loading with mtime-aware LRU + device cache; legacy-category remap. |
| `api.py` | 216 | Same-origin HTTP routes backing the frontend (`/flarecore/elements`, `/flarecore/presets` + `index`, `/flarecore/prompt_bank` + styles, `/flarecore/element_preview`, `/flarecore/save_preset`, `/flarecore/element/<ref>`). |
| `preset.py` | 42 | **FlarePresetLoader** — rescans `presets/`, outputs preset JSON. |
| `depth.py` | 59 | **FlareDepthAdapter** — standalone depth conditioning node. |
| `video.py` | 155 | **FlareKeyframes** — hand-animated `frame: u,v` light paths; `track_clip()` pipeline helpers. |

### Nodes registered (`NODE_CLASS_MAPPINGS`)

`FlareRender, FlarePresetLoader, FlareDepthAdapter, FlareKeyframes,
FlareElementPrompts, FlareTexturePrepare, FlareElementSave,
FlareGeneratorSelect`.

---

## 5. The render pipeline (data flow, end to end)

`FlareRender.render()` in `nodes/render.py`:

1. **Parse & validate** the preset (`schema.load_preset`). Resolve any
   `texture` element's `params.file` to a linear tensor
   (`library.resolve_preset_textures`).
2. **Pick the compute device.** ComfyUI hands IMAGE tensors on the CPU; the
   node renders on `comfy.model_management.get_torch_device()` (lazy import)
   and returns results on the input's device. Rendering on the CPU would be
   ~30× slower at 1080p.
3. **Size the chunk.** A whole clip does not fit in VRAM (the working set
   peaks near 10× the frames in flight). Frames stream through in slices
   sized from free VRAM; everything needing full-clip context (tracking,
   occlusion smoothing, temporal depth) works on small per-frame CPU values.
4. **Resolve light positions** for every frame (`_resolve_lights`), by
   `position_mode` (see §8). Output is `lights_per_frame`: a list (length B)
   of lists of light dicts `{u, v, brightness, [au, av], [tid], ...}`.
5. **Scene-anchor** the lights (detect/track/lock modes) via
   `_anchor_to_scene`, then apply `_damp_travel` (the `light_travel` dial).
6. **Condition depth** (if a depth map is wired): normalize/invert/blur/
   temporal-smooth per slice, compute per-light `occlusion`, smooth it
   across frames.
7. **Convert** each light's `u,v` to grid coords and build the engine's
   per-frame light list (with anchor, brightness, occlusion, flicker index).
8. **Build light masks** (only if a preset element uses `light_mask`):
   scene luminance combined with a radial glow pool per light.
9. **Render each chunk** with `engine.render_batch`, composite over the
   (linear) plate, encode to sRGB, clamp if asked, and write into the
   preallocated output tensors on the input's device.
10. **Return** `(image, flare_pass, flare_alpha)` plus a `ui` payload for the
    editor: `fc_preview` (clean plate backdrop for the picker),
    `fc_light_src` (what actually placed the light), `fc_track` (the solved
    path, for "bake to path").

### Inside `engine.render_stack` (one frame)

For each light: compute the flare axis angle (`light→anchor`), then for each
enabled element accumulate every count-instance into the output via
`_accumulate_element`. That function:
- Places the element center along the axis (`axis.element_center`), applies
  `move` (translation lock), then `shift` (screen-space), then `pin` (lock
  to frame edge).
- Transforms grid coords into the element's local frame (rotate by `-rot`,
  divide by `scale*stretch`), applies `global.aspect` in screen-space.
- Evaluates trigger rules (border/center/light proximity → add brightness/
  scale/rotation/color).
- Evaluates the element function once per dispersion sample (R/G/B or a
  wider spectral ramp), applies the `shade` lit-edge ramp, colours it, blurs
  if `blur>0`, masks by the light pool if `light_mask>0`.
- Screen-space elements (lens dirt, orbs) render once per frame at frame
  center, driven by the strongest light, not once per light.

Finally: multiply by tint × global intensity, apply chromatic fringe.

### The coordinate system (critical to get right)

- **Grid coords** (engine internal): origin at frame center, `y ∈ [-1, 1]`
  top-to-bottom, `x ∈ [-aspect, aspect]`. Units are half-frame-heights.
  Pixel centers are sampled.
- **UV coords** (light positions, picker): `[0,1]`, origin top-left. Widget
  ranges are actually `-1..2` so a light can sit off-frame (the sun above
  the top edge — essential for `follow` mode).
- `uv_to_grid(u,v,h,w)` converts. `occlusion_radius` and `search_radius`
  are fractions of frame **height**. Element `shift` uses **half-height grid
  units**, so `shift=[0,1]` moves half a frame height. The intended `max_jump`
  convention is height fractions, but the tracker still has the aspect-ratio
  inconsistency listed in §12. Trigger distances also use half-height units.

---

## 6. The element model

Nine element types, each a pure function `fn(u, v, params) -> (H,W)` field
in `[0, ∞)`. Colour, dispersion, intensity are applied by the engine, not
the function.

| Type | Shape | Notable params |
|---|---|---|
| `glow` | Moffat-style radial halo | `softness`, `falloff` |
| `iris` | n-gon aperture ghost via angular folding | `blades`, `roundness`, `edge_softness`, `hollow`, `crescent` |
| `streak` | anamorphic line (exp along × gaussian across) | `length`, `thickness`, `count`, `curve`, `dash` |
| `ring` | thin gaussian annulus | `radius`, `thickness`, `completion`, `crescent` |
| `hoop` | thick soft annulus fading toward the axis | `radius`, `thickness`, `angular_falloff`, `crescent` |
| `glint` | starburst; `points:1` = one-sided ray | `points`, `length`, `thickness`, `length_jitter` |
| `spectral` | ring or iris meant for high dispersion | `shape`, + ring/iris params |
| `texture` | samples a library PNG through the same transform pipeline | `file`, `channel` |
| `orbs` | procedural out-of-focus lens specks | `count`, `size`, `spread`, `ring`, `spectral`, `illumination` |

### Common element keys (all types)

`offset` (t along the axis: 0=light, 1=anchor, <0 behind), `scale`,
`stretch [x,y]`, `move [x,y]` (fraction of light motion followed per screen
axis — `[1,0]` = horizontal-only anamorphic ghost), `rotation`,
`auto_rotate`, `intensity`, `color` (linear, may exceed 1), `blur`,
`irregular` (0..1 seeded organic unevenness), `dispersion` +
`dispersion_samples`, `count`/`spread`/`count_falloff`/`count_scale_step`
(instance chains), `light_mask`/`mask_scene`/`mask_floor` (lens-plate
reveal), `fill_frame`, `screen_space`, `trigger` (rule block).

**Controls added from the cine-lens survey** (all default off):
- `shift [x,y]` — screen-space offset applied *after* the axis, so a
  companion ghost keeps a fixed drop below the source regardless of angle.
- `pin [x|null, y|null]` — lock one coordinate to the frame (y=-1 rides the
  top edge above the light).
- `shade` (-1..1) — linear brightness ramp across the element's own axis: a
  ghost lit only on the source-facing edge (comet / cusped crescent).
- streak `curve` (bowed line) and `dash` (segmented line).
- `crescent`/`crescent_feather` on round types — a clipping disc of the
  element's own radius, the way a barrel clips a reflection.
- orbs `ring`/`ring_width`/`spectral` — specks on a rim, each diffracting
  its own colour (dusty front-element reflection).

**2026-09-07:** iris and spectral-iris `params.roundness` blends the polygon
boundary toward a circle (0..1, default 0), independently of edge softness.
This is an artist-controlled approximation, not a mechanical blade model.
`glint.points=1` now actually evaluates one ray; the earlier implementation
clamped it to two despite the schema allowing one. Existing one-ray presets
therefore intentionally change appearance; set `points=2` to recover the
previous two-sided result.

---

## 7. Presets & the library taxonomy

### Preset format

Versioned JSON (`schema_version: 1`). Minimal valid preset:
`{"schema_version": 1, "elements": [{"type": "glow"}]}`. Every key has a
default. Unknown element types raise; unknown extra keys warn (forward
compatibility). Top-level: `name`, `author`, `category`, `subcategory`,
`global` (intensity/scale/aspect/tint/seed/fringe/flicker/edge-fade),
`elements`.

### Two taxonomies, each with one owner (`flare/schema.py`)

- **Element families** — 8: `glows, ghosts, rays, streaks, rings, hoops,
  caustics, lens_dirt` (`ELEMENT_SLOTS`). The *same* 8 name the folders
  under `elements/`, the top-level keys of the prompt bank, the editor's
  gallery filter (`CATEGORY_OF`), and every preset element's `slot`. A
  `slot` is which library shelf opens when you click an element's name.
- **Preset categories** — `Anamorphic, Spherical, Scenario, Utility`
  (`PRESET_CATEGORIES`), with an optional `subcategory` for genuine sibling
  sets (coating variants, focal variants). The category lives *inside* the
  preset, never in the filename — `FlarePresetLoader` stores filenames in
  saved workflows, so renaming to sort would break existing graphs.

`tests/test_library.py` (179 tests) pins all of it: the four element
taxonomies must agree, every preset must name a known category, every
element a known slot + label, every referenced texture must exist, a
texture must sit in the family its slot claims, and each element type has a
set of sensible slots.

The 23 lens-character presets were built from a frame-by-frame survey of 44
real cinema lenses; two findings shaped the library — *geometry belongs to
the optics, colour to the coating* (hence coating-variant siblings), and
*ghost size scales with focal length* (`global.scale`, hence tele variants).
Tuning was done by rendering contact sheets and looking, not by metric.

---

## 8. Light placement & the video subsystem

`position_mode` (a FlareRender widget) chooses how the light is placed each
frame. All modes are batch-native.

| Mode | What it does |
|---|---|
| `manual` | The picker's light point, same every frame. |
| `detect` | Brightest *region* per frame (gaussian-weighted centroid; resists clipped-sky plateaus). |
| `detect_with_manual_offset` | `detect`, then shifted by the picker's offset from center. |
| `track` | Per-frame detections associated into stable tracks across the clip; smoothed zero-phase, held/faded when lost. |
| `track_dots` | A black matte with white dots as a control layer; every dot gets its own flare with its own identity. Threshold relative to the clip's brightest pixel. |
| `path` | Follow a Catmull-Rom path drawn on the picker (`light_path` string). `anchor_path` optionally moves the anchor too (from "bake to path"). |
| `lock` | Whole-clip Viterbi solve: the trajectory explaining every frame with least total travel, so a single bad frame can't hand the light to a rival. |
| `point_track` | AE-style feature tracker: follow one or two contrast features (`track_points`); with two, the axis inherits their rotation/scale. |
| `follow` | Track the *camera*, not the light. The light is placed (can be off-frame) and carried by scene motion (Shi-Tomasi + LK + rigid fit). For a sun above the frame. |

**Scene anchoring** applies to detect/track/lock: the light is carried from
its most confident frame by the camera's motion, and only the detector's
*disagreement* with that is smoothed and clipped. `scene_lock` sets the
strength (1 for a sun, 0 for a self-moving light like headlights). A matte
with no scene is left alone automatically.

`light_travel` (1 follows the tracked path, 0 pins the flare still) damps
each light about its own center. `FlareRender`'s optional `lights` input
(FLARE_LIGHTS, from FlareKeyframes) overrides `position_mode` entirely.

**Occlusion**: feed `FlareRender.depth` from any depth source (Depth
Anything, MiDaS, Zoe, a Z-pass). `light_depth` is where the light sits on
the depth scale (0 = infinity). A sample occludes if it reads
`light_depth + 0.1` nearer. Off-frame samples are ignored (a fix so an
off-frame sun isn't blacked out by foliage on the frame edge).

---

## 9. The element forge

The forge generates new flare textures with an image model and files them
in the library. **The engine never runs a model** — the forge is a separate
node chain the user wires up.

Pipeline: `FlareElementPrompts` (pick a bank prompt + optional style tail) →
an image generator → `FlareTexturePrepare` (make it compositing-safe:
black-floor, autocenter, feather, 2K, per-category framing) →
`FlareElementSave` (file it in `elements/<family>/`).

- **Prompt bank** (`prompts/element_prompts.json`, 80 entries): natural-
  language Krea-2-style prose, subject first, physics named, one deliberate
  imperfection per element, no brand names.
- **Style tails** (`prompts/element_styles.json`, 32 in 6 groups): real
  shooting conditions appended to a prompt — lens era, capture medium, lens
  condition, atmosphere, light source, filtration. No moods, no brands.
- **Two generators, one switch** (`FlareGeneratorSelect`): one IMAGE output,
  two *lazy* IMAGE inputs (`image_a`/`image_b`), an `a`/`b` combo.
  `check_lazy_status` returns only the chosen input, so ComfyUI never
  executes the unselected branch — a hosted API generator parked on the
  unused input costs nothing while the local one runs. The frontend also
  mutes the upstream nodes of the unselected branch so it *looks* disabled,
  and re-applies that after the studio bench switch wakes the forge.
- Shipped studio wiring: prompt node feeds both a local **Krea2** subgraph
  (`image_a`) and a **GPT Image 2** API node (`image_b`); switch → prepare.
  Default `a` (Krea). GPT Image 2 needs the user signed into the Comfy API
  in their browser — there is no headless/API-key path (a known limit).

---

## 10. The frontend (`web/flarecore_ui.js`)

The main file registers a ComfyUI extension, with a separate response-editor
module. Major pieces:

- **PointPicker** — a canvas over the last render; drag the light and anchor,
  draw paths, place trackers, paint trigger regions and the solved track.
- **FlareEditor** — the stack editor: add/reorder/solo/delete elements,
  per-row sliders + color swatch, a twirl-down for every other key,
  undo/redo over the preset text, copy/paste, name-click gallery, the
  presets menu (grouped by category).
- **Forge panel** — category/element dropdowns, editable prompt box, the
  grouped style dropdown, extra-style toggle.
- **Studio switch** (`FlarecoreStudioSwitch`, a virtual node) — mutes the
  two inactive benches by group membership.
- **Generator switch glue** — mutes the unselected generator's upstream.

It communicates with the backend by reading/writing the `preset_json`
string widget and calling same-origin `/flarecore/*` routes.

### Frontend gotchas (hard-won; a new dev WILL hit these)

Running list of **dead idioms** in the current ComfyUI frontend (≈1.51.9):

- `widget.type = "hidden"` is INERT. Hide a widget with `widget.hidden = true`.
- `widget.computeSize` is IGNORED for DOM widgets. Report fluid height via
  `options.getMinHeight` (exposed as `computeLayoutSize().minHeight`).
- A custom node class MUST `extends LiteGraph.LGraphNode` and call `super()`;
  a plain class gets no prototype grafting and breaks other packs.
- `group.recomputeInsideNodes()` needs a canvas draw first (relies on a
  bounding cache). Compute membership yourself (node-center vs group rect)
  before the first draw.
- The in-app/preview browser reports `visibilityState: hidden` and
  `innerWidth/Height: 0`, so the rAF draw loop never runs and DOM widgets
  are 0px wide. Layout **cannot** be verified through it — only graph state,
  prompts and API behaviour can. `setTimeout` is throttled there too.
- `convertToSubgraph` freezes each node's *current mode* into the subgraph
  definition; a subgraph built from a muted bench ships a dead definition.

**Diagnosing UI reports:** always have the user hard-reload (Ctrl+Shift+R)
FIRST — the browser caches the extension module, and a stale cached
`flarecore_ui.js` has produced multiple phantom bugs. Verify the served
file (fetch it with a cachebust) before trusting behaviour. Two "panel
collapse" hunts were ultimately just the cached old module.

**Verifying JS parses** (there is no JS test runner): the file uses ES
module `import` at the top, so
`Get-Content -Raw web/flarecore_ui.js | node --input-type=module --check`
(PowerShell).
A duplicate top-level `const` silently kills the whole extension and
script-mode parsing can stop at the import before checking later declarations.
Syntax checking alone does not test browser behavior or Python/JS parity.

---

## 11. Testing

Run `pytest` with the ComfyUI portable interpreter (see §13); current results
are recorded in `REALISM_UPGRADE.md`. The original baseline reported 549 tests.
Notable suites:

- `test_engine.py`, `test_elements.py`, `test_pro_features.py`,
  `test_lens_params.py` — the field math and every element control.
- `test_nodes.py` — the node contracts (registration, shapes, the generator
  switch, composite identity).
- `test_video.py` — tracking, dot mattes, drawn paths, chunk invariance,
  `light_travel`.
- `test_scene_motion.py`, `test_feature_track.py`, `test_detect_occlude.py`
  — the tracking/detection/occlusion subsystems (including off-frame lights).
- `test_schema.py` — preset validation and range guards.
- `test_library.py` — the taxonomy enforcement and the style bank.
- `test_workflows.py` — the shipped templates (incl. subgraph liveness).

**Legacy frontend↔backend parity** remains a structural risk: ~20 constant tables
(defaults, ranges, enum lists, the trigger/path math) exist in *both* the JS
and Python. Legacy parity was verified by hand. The new motion module has
executed JavaScript tests against Python-generated reference values; this
does not establish parity for all of the older UI tables.
The recommended fix (not yet done) is to serve the contract from Python (a
`/flarecore/schema` route the JS consumes) so the tables have one owner.

---

## 12. Known issues / open items

From an internal audit (`docs/AUDIT_2026-09-06.md`), roughly by priority:

- **User data is written inside the package.** `save_preset` writes to
  `presets/`, `FlareElementSave` to `elements/`. A Registry reinstall or
  folder-replace wipes saved looks and forged textures. Should move to
  ComfyUI's user directory. (Documented as an alpha limit.)
- **Legacy JS/Python tables can drift.** Motion curves now have executed
  JavaScript parity tests and a Python-owned range endpoint; older tables
  still need the same treatment. See §11.
- **`point_track` concatenates the whole clip on the GPU** (defeats
  chunking; OOM risk at 4K). Should stream like the other modes.
- **The tracker's `max_jump` gate mixes width- and height-fractions**
  (`hypot(du,dv)`), so it is aspect-asymmetric on non-square frames.
- **Half-precision calculation fixed in this revision:** float16/bfloat16
  inputs now calculate in float32, including texture sampling, and cast at
  output. Float64 callers retain float64 computation. Final half-precision
  storage can still quantize gradients or overflow extreme HDR values; use
  float32 input/output for demanding HDR delivery. No speedup is claimed.
- **Per-element device syncs** in the tracking loops (`.item()`/`float()`),
  and the clip is sRGB-decoded 3–4×; the render is Python-dispatch-bound
  (~120 ms for one 960×540 frame on a 5090). Bounding-box element
  evaluation and batching one element across a chunk's frames are the top
  perf levers.
- **Registry metadata** (`pyproject [tool.comfy]` PublisherId/Icon) is empty.

Realism levers not yet built: scene-wide halation (`out += k·blur(plate)`),
using detected source `energy` (size) to scale ghosts, lens dirt lit by all
lights rather than the strongest by default. The new opt-in
`screen_blend: "all"` implements summed per-source illumination for
screen-space elements; existing presets retain `"strongest"`.

The blur formula above is only a proposed artistic bloom operation, not a
validated film-halation model. A useful extension needs highlight isolation,
defined radius/exposure behavior and tests preventing a uniform plate from
acquiring an unintended exposure lift. Detected energy is brightness times
area in an image neighborhood, not a calibrated source diameter.

### Limits of the realism claim

FlareCore is a procedural compositing model. It does not trace an optical
prescription, solve wavelength-dependent coatings or derive diffraction from
the aperture. Fixed RGB tints and coordinate-scaled dispersion are artistic
controls. Generated textures are synthetic interpretations, not photographed
measurements. The lens-survey conclusions in §7 are historical tuning notes,
not universal laws about focal length or coating behavior.

For comparison, Hullin et al.'s [Physically-Based Real-Time Lens Flare
Rendering](https://light.informatik.uni-bonn.de/physically-based-real-time-lens-flare-rendering/)
explicitly models lens aberrations, imperfections and antireflective coatings.
This reference establishes a useful future research direction; FlareCore
does not implement that paper's optical model.

---

## 13. Environment, build, test

- **Language/runtime**: Python 3.10+ (dev on 3.12), PyTorch (CUDA/MPS/CPU),
  numpy, Pillow. Frontend is plain ES-module JavaScript, no build step.
- **Dependencies**: `requirements.txt` = torch, numpy, Pillow. (ComfyUI ships
  torch+numpy; Pillow is the one that may be missing.)
- **Dev install**: the repo is placed (or junctioned) into
  `ComfyUI/custom_nodes/comfyui-flarecore`.
- **Run tests** with ComfyUI's interpreter so torch matches:
  `<comfy>/python_embeded/python.exe -m pytest -q`
- **Restart the running ComfyUI server** after a backend change:
  `POST http://127.0.0.1:8188/api/manager/reboot` with
  `Content-Type: application/json` (a simple-form content-type is rejected
  400 by the Manager's CSRF guard). Verify via `/object_info`. **Frontend
  changes need a hard browser reload**, backend changes need the server
  restart. `IS_CHANGED` on FlareRender folds the engine's mtime into the
  cache key, so editing the engine invalidates cached renders the same way
  turning a knob does.

### Repo layout

```
__init__.py            package entry, node mappings, WEB_DIRECTORY
flare/                 the engine (pure PyTorch)
nodes/                 ComfyUI node wrappers + HTTP routes
web/flarecore_ui.js    frontend extension
presets/*.json         41 shipped presets
prompts/               element prompt bank + style tails
elements/<family>/     218 element textures, 8 families
example_workflows/     flarecore_studio.json, flarecore_trigger_lab.json
tests/                 pytest suite (624 passed), motion JS parity tests
docs/                  DECISIONS.md (every design choice + rationale), this report, AUDIT
```

`docs/DECISIONS.md` is the deep record — every deviation from the original
spec and every non-obvious choice, with the reasoning and often the measured
numbers behind it. Read it before changing engine behaviour.

---

## 14. How to extend it safely

### Add an element type
1. Write `def newtype(u, v, p) -> (H,W)` in `flare/elements.py`; register it
   in `ELEMENT_FUNCTIONS`.
2. Add its `params` defaults to `PARAM_DEFAULTS` in `schema.py` and validate
   them in `_validate_element`.
3. Mirror the param ranges in `web/flarecore_ui.js` `PARAM_SPECS` /
   `PARAM_FALLBACKS`, add an `ADD_DEFAULTS` entry and an `ADD_MENU` line,
   and a `CATEGORY_OF` mapping.
4. Add tests. `test_library.py::TestSlotsSuitTheirType` needs a slot rule.

### Add a common element key
1. Default in `ELEMENT_COMMON_DEFAULTS`, validate in `_validate_element`.
2. Read it in `engine._accumulate_element`.
3. Mirror in JS `COMMON_SPECS` + `COMMON_DEFAULTS`, add a slider/checkbox row
   and a tooltip.
4. **Do not** add a widget mid-list on any node — append only (§2.4).

### Add a preset
Write JSON into `presets/`, or extend the generator script and re-run it.
Give it a `category` (+ `subcategory` only for a real sibling set), and give
every element a `slot` and `label`. Render a contact sheet and look at it —
there is no metric for "reads like a real lens".

### Add a node
Register in both `nodes/__init__.py` and `__init__.py`
(`NODE_CLASS_MAPPINGS` + `NODE_DISPLAY_NAME_MAPPINGS`); the registration
contract test lists all nodes and will flag it.

---

## 15. One-paragraph summary for the next LLM

FlareCore renders cinema lens flares as pure-PyTorch parametric math on a
normalized grid, wrapped as ComfyUI nodes, with a JSON preset describing a
stack of typed elements (glows, ghosts, streaks, rings, starbursts, orbs,
textures) placed along a light→anchor axis. It is batch-native (video =
image batch) with nine light-placement modes including camera-motion
tracking, depth occlusion, and a two-generator "forge" for making new
element textures. The engine (`flare/`) is model- and I/O-free and fully
tested; the node layer (`nodes/`) handles ComfyUI, device, sRGB and I/O; a
single JS file (`web/`) is the editor. The load-bearing rules are:
linear-light HDR throughout, identity-stable seeding for frame stability,
append-only node widgets, and a strictly enforced element/preset taxonomy.
The biggest open work is moving user data out of the package dir, testing
the JS↔Python contract, and the perf levers (bounding-box evaluation,
killing per-frame device syncs). Read `docs/DECISIONS.md` for the why behind
every choice before changing engine behaviour.
