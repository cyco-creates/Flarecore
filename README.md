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
