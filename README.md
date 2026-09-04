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
