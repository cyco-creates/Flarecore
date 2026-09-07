# Flarecore

Procedural, physically-inspired lens flares for ComfyUI. Flarecore combines a
linear-light renderer, a curated optical library, a motion-aware source system,
depth and image visibility, and an editor designed for real shots rather than
static demo images.

![Flarecore cover](Flarecore_cover.png)

## What Flarecore is

Flarecore renders an element stack from a light source toward a movable flare
anchor. The stack can contain procedural glows, ghosts, rays, streaks, rings,
hoops, caustics, lens orbs and photographed textures. Library textures move
with the same controls as procedural elements.

It runs on CUDA, MPS or CPU, needs no model to render a flare, and keeps the
image in linear light until the final output. Optional Studio branches add depth
models or image generators when you want them.

## Start here

Install the extension, restart ComfyUI, then open:

**Workflow → Browse Templates → flarecore → Flarecore Studio**

The Studio contains three benches:

- **Flare Lab** for still images, depth conditioning and look development.
- **Video Lab** for moving sources, obstruction and tracking.
- **Element Forge** for preparing reusable photographed or generated elements.

For a small graph, use:

**Load Image/Video → Flarecore · Render → Combine/Video Combine**

Flarecore Render is batch-native: in ComfyUI, a video is an image batch. The
node can place the source, track it, apply visibility and depth, render the
stack, and return the outputs without a separate tracker node.

## Installation

1. Copy or unzip this folder into `ComfyUI/custom_nodes/comfyui-flarecore`.
2. Install the small dependency set with the Python environment used by
   ComfyUI:

   ```text
   python_embeded\\python.exe -m pip install -r ComfyUI\\custom_nodes\\comfyui-flarecore\\requirements.txt
   ```

   ComfyUI installations normally already provide PyTorch and NumPy; Pillow is
   the main package this extension may need.
3. Restart ComfyUI and hard-refresh the browser (`Ctrl+Shift+R`).

Your saved presets and custom elements live in `presets/` and `elements/`.
Back them up before replacing the extension folder.

## Flarecore nodes

- **Flarecore · Render** — renders the composite, a flare pass over black, and
  an alpha/luminance mask. It includes the point picker, preset gallery,
  element editor, source controls, tracking, visibility and multi-flare groups.
- **Flarecore · Preset Loader** — loads a JSON preset from `presets/`.
- **Flarecore · Depth Adapter** — normalizes, flips, remaps and smooths an
  externally supplied depth map.
- **Flarecore · Keyframes** — provides authored light/anchor paths.
- **Flarecore · Element Forge** — edits prompts and prepares reusable element
  textures.
- **Flarecore · Prepare Texture** and **Flarecore · Save Element** — turn an
  image into a compositing-safe library element and save it.
- **Flarecore · Generator Select** — switches between optional element
  generators without running the unselected branch.

## Six light-source jobs

The source menu has six jobs. Related algorithms are grouped under one job so
the editor stays understandable while saved workflows retain their modes.

| Job | Use it for |
| --- | --- |
| **Place light** | Manual placement with the picker. |
| **Detect bright sources** | Find bright regions independently in each frame; an optional picker offset corrects a systematic detector shift. |
| **Track bright sources** | Match between frames, solve the complete clip, or track a dot matte with multiple source identities. |
| **Follow camera motion** | Carry a placed or off-frame source with the camera, useful when the light is not a clean dot. |
| **Track a chosen feature** | Follow a textured edge or detail, independently of brightness; two features also drive axis rotation and scale. |
| **Draw / edit a path** | Author, refine or bake the source route yourself. |

For a sun passing behind trees, use **Track bright sources → Solve entire
clip**. It keeps source identity through hidden intervals, interpolates between
trusted observations and limits endpoint extrapolation. For an overexposed sun
with no texture, source tracking is preferable to feature tracking. For a
moving headlight, reduce scene lock so the light is allowed to move independently
of the camera.

Every tracked source also has a travel control: 1 follows the measured path, 0
holds the flare in place, and intermediate values scale the excursion. Results
can be inspected and baked into an editable path.

## Obstruction and visibility

Flarecore can combine depth coverage with measured source brightness. In the
default **Image + depth** mode, branches and other foreground objects make the
flare dim and shrink instead of merely leaving the light position unchanged.

The visibility choices are:

- **Image + depth** — default; combines image evidence with depth.
- **Image visibility** — useful when no depth map is available.
- **Depth only** — let the depth map control obstruction by itself.
- **Off** — disables automatic image/depth attenuation while preserving any
  explicitly supplied per-light obstruction.

Image visibility needs at least one clear view of the source in the batch and
can respond to exposure changes. A completely hidden source cannot be recovered
from image evidence alone. See [Tracking and visibility](docs/TRACKING_AND_VISIBILITY.md)
for source radius, smoothing, depth conventions and limitations.

## Multiple flares in one Render node

Flarecore Render supports up to 16 independent flare groups. Add, duplicate,
rename, enable or remove groups in the editor. Each group has its own:

- preset and element stack;
- master brightness/size, base intensity/scale, aspect and lens settings;
- light and anchor positions;
- source mode, tracking/path settings and visibility controls;
- depth conditioning, tint and seed.

Groups inspect the original image and depth, then their HDR emissions are summed
and composited once. Save the **workflow** to preserve the complete scene;
**Save preset** saves the selected group's look.

## Editor and gallery

The Render editor provides a point picker for the light and anchor, a stack
editor with undo/redo, and a preset gallery with category filters and complete
16:9 previews. Hovering previews a card; clicking pins the large preview on the
right until another card is clicked. Loading or merging is explicit, and the
selected preset remains visible in the toolbar.

The global **Master** control scales both flare brightness and size. Base
intensity and scale remain available separately under Lens. Element controls are
organized into Shape & appearance, Position & orientation, Repeated elements,
Masking & lens space, and Optical response.

Optical response curves animate opacity, size, aspect, rotation, color and
supported shape properties from source position. Use the response presets for
common behaviors such as edge emergence, gentle breathing and ghost movement.

## Building realistic flares

The library is organized around optical families. Presets cover anamorphic,
spherical and scenario looks, with
coating variants such as blue, amber, silver and clear. Element families are:

`glows`, `ghosts`, `rays`, `streaks`, `rings`, `hoops`, `caustics`, and
`lens_dirt`.

Useful realism controls include seeded irregularity, chromatic fringe,
completion/arc shaping, spectral dispersion, anamorphic aspect, source-driven
illumination, lens plates, scene-color tinting and sub-pixel anti-aliased rays.
Random detail is seeded so a video holds together instead of re-rolling every
frame.

The Element Forge includes an editable prompt bank and optional Krea2/GPT Image
2 generator branches. Prepared textures are centered, feathered and given a
black floor so they can be reused without unexpected cropping.

## Outputs and compositing

`image` is the finished composite, `flare_pass` is the flare over black, and
`alpha` is the flare mask. `add` is the physically correct linear-light blend;
`screen` is available as a softer convenience mode. With clamping disabled,
the flare pass can retain HDR values for downstream compositing.

## Documentation

- [Studio guide](docs/STUDIO_GUIDE.md) — editor, groups, gallery, depth and
  Element Forge workflow.
- [Tracking and visibility](docs/TRACKING_AND_VISIBILITY.md) — obstruction,
  source solving and known limits.
- [Optical response](docs/OPTICAL_RESPONSE.md) — position-driven animation.
- [Realism collection](docs/REALISM_UPGRADE.md) — optical library additions.
- [Flare anatomy study](docs/FLARE_ANATOMY_STUDY.md) — observed flare features
  and design rationale.
- [Documentation index](docs/README.md) — user guides and references.

## Requirements and limits

The renderer does not estimate depth by itself; connect a depth model or a
rendered Z-pass when needed. The Studio's optional depth, video and generator
branches may require additional ComfyUI custom nodes, models or a provider
login. Disconnect optional branches you do not use.

Tracking through complete occlusion is an estimate, not a physical
reconstruction. Image visibility can be influenced by exposure changes and
needs a clear reference. Always inspect the flare-only pass and tracking
confidence before baking a path for a final shot.

## License

Apache-2.0. Flarecore is an original procedural renderer and does not read or
write proprietary preset formats.
