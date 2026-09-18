# FlareCore Studio guide

## Pick a bench

Open `example_workflows/flarecore_studio.json`, or the saved `flarecore_studio` workflow in ComfyUI. Use the existing Studio switches to enable the bench you need.

- **Flare Lab:** compose a flare over a still image, with depth occlusion available.
- **Video Lab:** apply the same system to footage and moving lights.
- **Element Forge:** create and prepare reusable texture elements.

Load a source image and run once to populate the picker. Choose a preset, then drag the orange light and cyan flare anchor. Use the element row's position, size, opacity, blur, and color controls for everyday adjustments.

## Preset gallery and master control

The top toolbar shows the selected preset name. Open it to browse searchable
thumbnails; hover or focus a card to see a larger preview of the actual preset,
including its texture elements. Previews use a fixed source position on black,
not the current scene. Click a thumbnail to pin the right-hand preview: hovering
or focusing other cards no longer changes it until you click another thumbnail.
The pinned card has a stronger amber outline. Filtering preserves this choice.
Previewing never changes the stack. Choose Load preset to
replace it, or Merge elements to append elements while retaining global settings.
Loading, merging and title changes participate in Undo/Redo. The selected name
is stored with the preset JSON and survives workflow saves.

Use the category dropdown to filter the gallery. Every card shows a complete
16:9 thumbnail, preset name and category; cards scroll instead of shrinking to
fit the list. Full-frame glow or streak effects can naturally extend beyond the
preview's image boundary, just as they do in the final render.

Master now multiplies both brightness and size: 1 retains the preset's original
balance, 0 turns the flare off. Existing preset brightness and size values are
preserved as independent base settings, available under Lens. This is an
additive-light energy control, not ordinary paint-layer alpha.

## Multiple flare groups in one node

The Flare Groups row sits directly below the toolbar. **+ Flare** adds a new,
empty flare instance; choose a preset for it. **Duplicate flare** copies the
selected group's full look and source configuration, ready to position elsewhere.
Select a group tab before editing. Rename, Enabled and Remove affect that group;
Undo/Redo covers group creation, duplication, removal, and settings.

Each group has its own element stack, master, base brightness/size, aspect,
tint, lens effects, source/anchor positions, tracking mode and paths, visibility,
depth conditioning and seed. Use Source tuning for numeric source/anchor positions
and **Group depth, colour and seed** for the less frequently used settings.
The picker edits the selected group; grey markers identify other enabled groups.
Tracking results and baked paths belong to the selected group.

The original image and depth input are shared. All groups inspect the original
scene, never another group's rendered flare. Their HDR emission is summed in
linear light and composited once. Image color space, output blending/clamping,
chunk size, and the node-level intensity/scale controls remain scene-wide.
With an external lights connection, choose **Use connected lights for this group**
only on groups that should use it; other groups can still use their own sources.

Save the **workflow** to retain the complete multi-flare scene and source settings.
The toolbar's Save preset saves the selected group's look, not the complete scene.
Groups use an optional JSON scene container; existing single-flare workflows stay
unchanged until a group operation is used. Up to 16 independent groups are supported.
More groups require more rendering/tracking work; they are processed sequentially
to avoid multiplying GPU memory usage by the number of groups.

## Element controls

Expand an element row to reveal five focused sections. Shape opens initially; the others stay compact until needed. Open/closed state survives edits and editor rebuilds within the current session.

| Section | Use it for |
| --- | --- |
| Shape & appearance | Element-specific shape, irregularity, shading, and color separation |
| Position & orientation | Rotation, axis following, stretching, translation, and frame pins |
| Repeated elements | Copies, spacing, size progression, and fading along a chain |
| Masking & lens space | Scene/light masks, full-frame plates, and multi-light illumination |
| Optical response | Curves controlling how the element changes as the source moves |

Optical response is the animation editor for moving elements. Choose a response
starting point, then shape its curves for the selected element. Curves can drive
opacity, size, aspect, rotation, color and supported shape properties. Stack
Undo restores the previous curve settings.

## Light sources: six jobs, not nine competing modes

| Job | Options / reason to keep it |
| --- | --- |
| Place light | Direct placement without detection |
| Detect bright sources | Bright-region detection; optional picker offset |
| Track bright sources | Frame matching, whole-clip solving, or a dot-matte method |
| Follow camera motion | A placed source carried by scene motion, including off-screen sources |
| Track a chosen feature | Explicit one/two-feature tracking, independent of brightness |
| Draw / edit a path | Authored or baked motion |

Detection offset is a modifier, not a separate job. Frame matching, whole-clip
solving and dot mattes are available under Track bright sources. Whole-clip
solving uses the complete batch, while frame matching follows sources from one
frame to the next. Feature tracking has its own patch, search and smoothing
controls.

Switching modes preserves your tuning. Use Recommended settings when you want a
good starting point for the selected method. A connected lights input takes
priority over the source selector.

## Element Forge

The prompt node has labeled Family and Element selectors, a larger generation-prompt editor with character count and Restore base prompt action, and a collapsible Optional styling section. Saved custom text is retained on load. Selecting a different element loads that element's base prompt.

The bench flows left to right: define the element, configure a generator,
choose its branch, prepare the texture, inspect it, then save to the library.
The preview shows the prepared element. Krea2 and GPT Image 2 are optional
generator branches; only the selected branch runs.

## Depth in Flare Lab

For sources passing behind branches, see [Tracking and obstruction](TRACKING_AND_VISIBILITY.md).
Source tuning now exposes Obstruction, source radius and visibility smoothing.
The default Image + depth mode measures the source's visible area as well as
depth; a tracker retaining a position no longer guarantees full flare energy.

The image loader feeds both Flare Render and **Depth · Flare Lab**, placed below the loader. Depth Anything V2 feeds the renderer's `depth` socket. It uses the same preprocessor and model selection as Video Lab: `comfyui_controlnet_aux`, `depth_anything_v2_vitl.pth`, resolution 512. The dependency must be installed and its model available; a first run may need a model download.

Use the render settings to adjust light depth and invert the near/far convention when necessary. Disconnect the depth cable for a render without depth estimation. FlareDepthAdapter remains available as an optional node for additional conditioning of externally supplied depth maps.

The depth node is inside the Flare Lab group and follows that bench's enabled or
bypassed state.

## Finish and save

Save your preset from the stack editor. The composite is the finished image; flare pass is the flare over black for external compositing; alpha is the flare mask. Save the workflow too when you change connections or layout.

After installing UI changes, save any unsaved work before refreshing the ComfyUI page. Reopen the Studio workflow if you want to load its current bench layout; editing a saved workflow file does not change an already-open graph.
