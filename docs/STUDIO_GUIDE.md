# FlareCore Studio guide

## Pick a bench

Open `example_workflows/flarecore_studio.json`, or the saved `flarecore_studio` workflow in ComfyUI. Use the existing Studio switches to enable the bench you need.

- **Flare Lab:** compose a flare over a still image, with depth occlusion available.
- **Video Lab:** apply the same system to footage and moving lights.
- **Element Forge:** create and prepare reusable texture elements.

Load a source image and run once to populate the picker. Choose a preset, then drag the orange light and cyan flare anchor. Use the element row's position, size, opacity, blur, and color controls for everyday adjustments.

## Element settings

Expand an element row to reveal five focused sections. Shape opens initially; the others stay compact until needed. Open/closed state survives edits and editor rebuilds within the current session.

| Section | Use it for |
| --- | --- |
| Shape & appearance | Element-specific shape, irregularity, shading, and color separation |
| Position & orientation | Rotation, axis following, stretching, translation, and frame pins |
| Repeated elements | Copies, spacing, size progression, and fading along a chain |
| Masking & lens space | Scene/light masks, full-frame plates, and multi-light illumination |
| Optical response | Curves controlling how the element changes as the source moves |

Optical response is the animation editor for new work. Existing legacy rules still render, but their old control panel is removed. Only elements carrying an old rule show a compatibility notice and a Remove old rule button (covered by stack Undo). There is no automatic conversion: old additive triggers are not equivalent to response multipliers. Applying a response starting point replaces that element's curves; stack Undo restores the previous state.

## Light sources: six jobs, not nine competing modes

| Job | Options / reason to keep it |
| --- | --- |
| Place light | Direct placement without detection |
| Detect bright sources | Bright-region detection; optional picker offset |
| Track bright sources | Frame matching, whole-clip solving, or a dot-matte method |
| Follow camera motion | A placed source carried by scene motion, including off-screen sources |
| Track a chosen feature | Explicit one/two-feature tracking, independent of brightness |
| Draw / edit a path | Authored or baked motion |

Detection offset is a modifier, not a separate job. Frame matching, whole-clip solving and dot mattes share one tracking entry but retain distinct algorithms: replacing them with a single implementation would change behavior. Whole-clip solving does not use Hold/Fade, so those controls are hidden for that method. Feature tracking now exposes its existing smoothing setting.

Switching modes or methods preserves tuning. Use Recommended settings when you deliberately want the mode's starting values. The underlying serialized mode identifiers remain compatible with saved workflows and external inputs. A connected lights input takes priority over the source selector.

## Element Forge

The prompt node has labeled Family and Element selectors, a larger generation-prompt editor with character count and Restore base prompt action, and a collapsible Optional styling section. Saved custom text is retained on load. Selecting a different element loads that element's base prompt.

The bench flows left to right: define the element, configure a generator, choose its branch, prepare the texture, inspect it, then save to the library. The preview is explicitly the prepared element, not confirmation that saving succeeded. The existing Krea2 and GPT Image 2 branches and their settings are preserved; no generation or paid request is performed by a UI change.

## Depth in Flare Lab

The image loader feeds both Flare Render and **Depth · Flare Lab**, placed below the loader. Depth Anything V2 feeds the renderer's `depth` socket. It uses the same preprocessor and model selection as Video Lab: `comfyui_controlnet_aux`, `depth_anything_v2_vitl.pth`, resolution 512. The dependency must be installed and its model available; a first run may need a model download.

Use the render settings to adjust light depth and invert the near/far convention when necessary. Disconnect the depth cable for a render without depth estimation. FlareDepthAdapter remains available as an optional node for additional conditioning of externally supplied depth maps.

The restored depth node is inside the Flare Lab group and has the same enabled/bypassed state as that bench. Switching benches therefore includes it.

## Finish and save

Save your preset from the stack editor. The composite is the finished image; flare pass is the flare over black for external compositing; alpha is the flare mask. Save the workflow too when you change connections or layout.

After installing UI changes, save any unsaved work before refreshing the ComfyUI page. Reopen the updated Studio workflow to load the restored depth node: editing a saved workflow file does not change an already-open graph.
