# comfyui-flarecore

Procedural lens flare rendering for ComfyUI. Pure PyTorch math on a coordinate
grid — no OpenGL, no textures, no models, no network calls. Works headless on
CUDA, MPS, and CPU.

## What it does

- Renders a stack of parametric flare elements (glow, iris ghosts, anamorphic
  streaks, rings, hoops, starbursts, spectral rings) along a physically
  motivated flare axis from the light position through frame center.
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
- **Stack editor** — add, reorder, duplicate and delete elements; every row
  has sliders for position along the axis, size, and opacity, and the
  twirl-down exposes everything else (color, dispersion, count chains,
  stretch, per-type parameters, texture file picker). It reads and writes the
  `preset_json` widget, so hand-edited JSON and the editor stay in sync.
  `save…` writes into `presets/`; `presets ▾` loads any shipped or saved look.

## Custom elements (element forge)

Two shipped workflows (ComfyUI → Workflow → Browse Templates → flarecore):

- **flarecore_flare_lab** — the full playground: scene, depth occlusion,
  editor, and all outputs.
- **flarecore_element_forge** — generate custom element textures with your
  local image model (wired for Krea2), condition them, and file them in the
  library. Pick a prompt from the bank, queue, then use the new element from
  the editor's texture dropdown. The engine itself never runs a model; the
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

See `presets/` for full examples and `flare/schema.py` for the complete key
reference. Unknown element types fail loudly; unknown extra keys warn.

## Notes for compositors

- `flare_pass` is the flare rendered over black, encoded to sRGB. With
  `clamp_output` off it is unclamped HDR. Adding the decoded pass over the
  decoded source in linear light reproduces the `image` output exactly.
- `add` is the physically correct blend mode. `screen` is provided as a
  soft-clipped convenience and operates on values clamped to [0, 1].

## License

Apache-2.0. Everything is procedural and original; no proprietary preset
formats are read or written.
