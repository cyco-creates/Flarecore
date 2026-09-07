# FlareCore

Procedural lens flares for ComfyUI. Depth-aware, keyframable, rendered in linear light.

> **Status: pre-release.** The render engine is in active development and nothing here is stable yet. The preset schema is versioned from v1 and will be kept backward-compatible, but node names and inputs may still change before the first tagged release. Not yet on the ComfyUI Registry.

---

## What this is

A node pack that renders lens flares as procedural math rather than as pasted texture sprites. Every element — halo, iris ghost, anamorphic streak, ring, glint — is a parametric function evaluated on a coordinate grid in PyTorch, on whatever device your image tensor already lives on.

The design goal is a flare that behaves correctly when the light moves, rather than one that looks acceptable in a single still.

## Why it's different from the other flare nodes

**Depth-aware occlusion.** Feed it a depth map and the flare fades as objects pass in front of the light source, instead of floating on top of the frame. Depth maps are already in most pipelines, which makes this close to free and is the main reason to use this over a simple overlay.

**Real light detection, with a manual override that actually overrides.** It can find bright points in the frame and place flares on them automatically, or take an explicit position from you, or take a detected position with a manual offset. Manual mode never runs the detector.

**A moveable anchor.** Flare elements are positioned parametrically along an axis running from the light to an anchor point. Most implementations hardcode that anchor to frame centre. Here it's yours to move, which is what lets you art-direct the ghost chain rather than accept the one layout the maths gives you.

**Keyframable.** Both the light position and the anchor can be animated across a batch, with linear, held, eased or spline interpolation. Built for sequences, not just stills.

**A flare-only pass.** Every render outputs the flare over black with alpha alongside the composite. If you're taking this into Nuke, Fusion or After Effects, that pass is the actual deliverable and it's correct in isolation.

**Linear light, float32, HDR headroom.** Flares composite additively in linear space. Doing this in sRGB is the most common reason procedural flares look like plastic, and it's the first thing this pack gets right.

## Design constraints

These are deliberate and won't change:

- **PyTorch only.** No OpenGL, no moderngl. Headless GL is unavailable or broken on most cloud ComfyUI installs, so everything is tensor math that runs anywhere ComfyUI runs.
- **No texture assets.** Everything is procedural. Nothing to license, nothing to ship, resolution-independent by construction.
- **The engine doesn't import ComfyUI.** `flare/` is a standalone library that the nodes wrap. You can use it outside ComfyUI, and it's testable without spinning up a server.
- **Batch-native, device-agnostic.** CUDA, MPS and CPU. No hardcoded device calls.
- **torch and numpy only.** No new heavy dependencies.

## Elements

`glow` · `iris` · `streak` · `ring` · `hoop` · `glint` · `spectral`

Each supports independent scale, stretch, rotation, axis-relative offset, colour, intensity, chromatic dispersion, and multiplicity for building long ghost chains from a single definition.

## Presets

Presets are plain JSON with an explicit `schema_version`. Minimal presets render — every omitted key falls back to a documented default. Drop a `.json` into `presets/` and it appears in the loader without restarting ComfyUI.

```json
{
  "schema_version": 1,
  "name": "Clean 35mm",
  "global": { "intensity": 1.0, "scale": 1.0, "tint": [1.0, 1.0, 1.0], "seed": 0 },
  "elements": [
    { "type": "glow", "offset": 0.0, "scale": 0.4, "color": [1.0, 0.95, 0.85],
      "params": { "softness": 0.35, "falloff": 1.2 } }
  ]
}
```

Preset contributions are welcome and are the easiest way to help.

## Roadmap

- [ ] **Phase 1** — render engine, seven elements, preset schema, render and loader nodes
- [ ] **Phase 2** — light detection, depth occlusion, keyframed light and anchor paths, temporal smoothing
- [ ] **Phase 3** — visual stack editor as a ComfyUI frontend extension
- [ ] **Phase 4** — preset library, documentation, Registry release

## Installation

Not yet available. Once Phase 1 is tagged, install via ComfyUI Manager or clone into `ComfyUI/custom_nodes/`.

## License

Apache-2.0. Chosen over MIT for the explicit patent grant, since this is aimed at people working inside studios.

## Prior art and independence

Procedural lens flare generation is a long-established technique in computer graphics and has been implemented many times across many applications. This is an independent implementation built from published optics and rendering principles. It contains no code, assets, preset data or file-format support derived from any commercial product.
