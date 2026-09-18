import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const file=path.join(root,'example_workflows/flarecore_studio.json');
const doc=JSON.parse(fs.readFileSync(file,'utf8'));
const node=id=>{const n=doc.nodes.find(n=>n.id===id);if(!n)throw Error(`Missing node ${id}`);return n;};
function note(id,title,text,pos,size){Object.assign(node(id),{title,pos,size,widgets_values:[text],widgets_values_named:{text},color:'#34343e',bgcolor:'#1a1a20'});}
note(29,'START HERE · FlareCore Studio',`FLARECORE STUDIO — MASTER GUIDE

START
Choose one bench with a Studio switch. Run once after loading your image or clip to populate the picker. Save your preset for a reusable look; save the workflow for its connections and settings.

THREE BENCHES
• Flare Lab: compose over a still image, with depth occlusion.
• Video Lab: use the same flare system on a clip.
• Element Forge: generate an element, prepare its black background, then save it into the library.

PLACE THE LIGHT
Orange = light source. Cyan = flare anchor. Ghosts align along the line between them. The anchor is an artistic control, not the physical optical center used by response curves.

LIGHT-SOURCE CHOICES
• Place light: drag a fixed source, including outside the image.
• Detect bright sources: locate bright regions. Optional Offset from picker shifts detections relative to the frame center.
• Track bright sources: Match between frames keeps source identities; Solve entire clip chooses a coherent path; Track a dot matte uses a clip-relative brightness threshold for white dots on dark backgrounds.
• Follow camera motion: place a source once and carry it with estimated scene motion. Useful for an off-screen source; depends on trackable scene detail.
• Track a chosen feature: place one tracker for position, or two for light and anchor motion. Use contrasty edges rather than blown highlights.
• Draw / edit a path: author the route directly. Bake to path converts a solved render into editable points.
An external lights connection overrides the picker source mode.

SOURCE TUNING
Threshold rejects dim detections; max lights caps the count. Smoothing reduces jitter; max jump limits association distance. Hold/fade bridge temporary loss in frame-matching and dot tracking. Search radius restricts detection around the picker. Scene lock biases supported modes toward scene motion. Travel reduces movement. Switching modes preserves tuning; Recommended settings explicitly restores a starting point.

BUILD THE LOOK
Add, duplicate, reorder, solo, or remove elements. Row controls adjust axis position, size, opacity, blur, and color. Click an element name for its family library; double-click to rename. The menu copies/pastes settings. Undo/redo reverses stack changes. Load, merge, and save presets.

ELEMENT SETTINGS
Shape & appearance: shape-specific controls, irregularity, shading, dispersion.
Position & orientation: rotation, stretch, movement, shifts, frame pins.
Repeated elements: copy count, spacing, fading and size progression.
Masking & lens space: source/scene masks, lens-fixed textures, full-frame plates and multi-light illumination.
Optical response: drive opacity, size, aspect, rotation, position, color and supported shape features from source position, brightness or occlusion. Start/End are editable endpoints; dashed tails hold the nearest value. Starting points replace curves; Undo restores them. Old triggers render only for compatibility; existing rules have an explicit removal action.

GLOBAL LOOK & OCCLUSION
Global intensity, scale, aspect and tint affect the stack. Lens controls add chromatic fringe, seeded flicker and off-frame fade. Scene color can tint the flare from the image. Depth controls set light distance, near/far convention, normalization, blur and temporal smoothing. Depth Anything V2 requires its preprocessor and model; disconnect depth for depth-free rendering. FlareDepthAdapter can condition other depth maps.

OUTPUTS & PERFORMANCE
Composite = finished image. Flare pass = flare over black. Alpha = flare mask. Choose blend mode and clamping for your composite; use the correct sRGB/linear setting for your input. Chunk frames limits working memory on clips. The settings gear exposes raw node inputs; they retain older mode names for compatibility.

FORGE & LIBRARY
Choose an element family, refine the base prompt, and optionally append styling. Select Krea2 or GPT Image 2 in the generator selector; provider credentials/models and possible API charges are separate from FlareCore. Prepare removes the black floor, centers and feathers the asset. Inspect the preview before saving. Category/name determine its library path; enable overwrite only when replacing an existing element.

SAVE & RELOAD
Save unsaved work before refreshing after an update. Reopen the saved Studio workflow for changed nodes and notes. Panels may hide at low canvas zoom. Detailed reference: docs/STUDIO_GUIDE.md and docs/OPTICAL_RESPONSE.md.`,[-4650,-700],[650,1590]);
note(13,'Element Forge · Quick start',`ELEMENT FORGE

01  DEFINE
Choose a family and element. Edit the generation prompt. Optional styling is appended only when enabled.

02  GENERATE
Choose a in the selector for Krea2, or b for GPT Image 2. Configure that generator's model, credentials and output settings. Running a paid provider may incur charges.

03  PREPARE
Tune black point, centering and feathering. Preview the prepared result. Keep natural detail without gray background contamination.

04  SAVE
Check category and name in Save to library. Leave overwrite off unless replacing an existing asset. The saved element becomes available in the matching family gallery.

The selector runs only the chosen generator branch.`,[-3900,-610],[330,530]);
note(21,'Flare Lab · Quick start',`FLARE LAB — STILL IMAGES

1  Load an image and run once.
2  Choose a preset in Flare Render.
3  Place the orange light and cyan anchor.
4  Adjust element rows; expand only the settings you need.
5  Save the preset and final composite.

DEPTH
The node below the image loader estimates depth and feeds Flare Render. Set light depth and invert near/far if needed. Disconnect depth if you do not need occlusion.

OUTPUTS
Right: composite, flare-only pass and alpha previews. Save Image writes the composite.

Use Optical response for element motion. The master note explains all source modes and controls.`,[-1490,-605],[340,510]);
note(27,'Video Lab · Quick start',`VIDEO LAB — MOVING LIGHT

1  Load a short clip while tuning.
2  Choose a flare preset.
3  Pick a source job:
   Track bright sources for clear lights.
   Follow camera motion for a placed/off-screen light.
   Track a chosen feature for a contrasty detail.
   Draw / edit a path for direct control.
4  Run and inspect the result.
5  Bake to path if a solved track needs hand correction.

TUNING
Start with Recommended settings, then adjust smoothing, search and travel as needed. Tracking methods solve different problems; see the master note.

DEPTH & OUTPUT
Depth Anything supplies occlusion. Match output frame rate to the source. The two output nodes write the composite and flare-only pass.

For longer clips, use chunk frames to limit rendering memory. Save the workflow and preset separately.`,[1276,-622],[400,590]);
const layout={
 1:['01 · Define element',[-3530,-610],[480,660]],
 30:['Krea2 generator',[-2990,-610],[420,180]],
 34:['GPT Image 2 generator',[-2990,-350],[420,340]],
 35:['02 · Generator: a = Krea2 / b = GPT Image 2',[-2510,-610],[330,100]],
 10:['03 · Prepare texture',[-2510,-410],[330,260]],
 12:['Prepared element · inspect before saving',[-2110,-610],[420,300]],
 11:['04 · Save to library',[-2110,-230],[420,180]],
};
for(const [id,[title,pos,size]] of Object.entries(layout))Object.assign(node(Number(id)),{title,pos,size});
node(31).pos=[-3940,-890];
doc.groups.find(g=>g.title==='ELEMENT FORGE').bounding=[-3940,-700,2300,900];
// Separate the video depth node from the expanded video-loader preview.
node(23).pos=[1750,0];
fs.writeFileSync(file,JSON.stringify(doc,null,2)+'\n');
console.log('Rewrote four notes; organized Forge; preserved links and widget settings.');
