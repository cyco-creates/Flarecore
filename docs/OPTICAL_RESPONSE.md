# Optical Response — using the flexible system

Restart ComfyUI after installing the Python changes and hard-reload the browser
with Ctrl+Shift+R. Open a FlareRender element's advanced controls. The new
**Optical response** panel sits at the top. No new positional node widget was
inserted, so existing workflow widget arrays keep their meaning.

## Quick start

1. Load an **Adaptive Optics** preset: Warm Glass, Blue Streak, Soft Prismatic
   or Clean Spherical. They are different starting stacks for the same engine.
2. Expand an element and choose its response property. Inspect how its energy,
   size or clipping varies with the source position.
3. Drag a knot, or edit its input and response numerically. An opacity/size
   response of 1 means unchanged; rotation is an added angle in degrees.
4. Move the light with the picker, a drawn path, a tracker or camera follow,
   and render. The curve display previews the response function, not a live
   optical render. Existing render/queue controls still generate the flare.
5. Use **Enable response** to compare against the static base without deleting
   your curves. Normal editor Undo covers curve changes and starting points.

For an existing element, **Gentle breathing**, **Edge emergence** and **Ghost
squeeze + clipping** provide editable curve sets. Applying one replaces that
element's existing motion curves. It does not change the base geometry or turn
off auto-rotation. For horizontal anamorphic streaks keep auto-rotate disabled.
For axis-aligned asymmetric ghosts use auto-rotate deliberately: the axis is
undefined when source and anchor coincide, so a direction change at that exact
crossing is a limitation of the existing axis model. Use fixed rotation plus
a signed X/Y response when continuous orientation through the center matters.

Use **+ Add response curve** for independent opacity, scale, width, height,
rotation, axis offset, screen shifts, softness, dispersion, edge shade,
shape-compatible clipping/roundness/streak bow, or RGB transmission. The
property list only offers unused compatible targets. Each has its own driver:
source radius, X, Y, distance inside the frame edge, brightness or occlusion.

**Units:** radius, edge distance and screen shifts use half-frame-height
units. X is normalized to -1 at the left edge and +1 at the right; Y uses
-1 at the top and +1 at the bottom. Response inputs hold endpoint values
outside the authored range. To fade an off-frame source out, explicitly put
a zero-opacity endpoint there or use the preset's global edge fade.

For a screen-space element, **lens illumination → all** sums all lights with
the same seeded pattern. This costs more than **strongest**, but avoids an
illumination jump when lights swap brightness. Keeping the pattern fixed still
requires leaving its geometry/transform response static.

## Preset contract

```json
{
  "motion": {
    "enabled": true,
    "channels": [
      {
        "target": "opacity",
        "driver": "radius",
        "interpolation": "smooth",
        "points": [[0, 0.2], [0.8, 1.0], [2.5, 0.0]]
      },
      {
        "target": "stretch_x",
        "driver": "radius",
        "interpolation": "smooth",
        "points": [[0, 1.0], [2.5, 0.5]]
      }
    ]
  }
}
```

This is an element fragment, not a complete preset. Each channel needs 2–16
finite knots in strictly increasing input order. Each target can occur only
once; there are 17 supported targets. Unknown properties, incompatible shapes,
NaN, duplicate positions and out-of-range outputs are rejected before render.
Absence of motion preserves legacy output; disabled motion retains its curves.

Python owns target ranges and driver labels in `flare/motion.py`. The UI loads
them from `/flarecore/motion_schema`, so backend updates need a server restart.
There is no duplicated fallback range table that silently diverges.

## Verification (2026-09-07)

- **624 Python tests passed in 30.01 seconds**, including new response and
  multi-source illumination regressions and the existing workflow contracts.
- JavaScript execution passed **20 Python/JS interpolation parity cases**,
  malformed-point guards and **24 starting-point/element-type combinations**.
- Both frontend modules parse as ES modules.
- Browser testing of the actual response component in an isolated workbench
  verified keyboard entry, dragging, point insertion, invalid-input rejection,
  serialized values and preservation of expanded channels after edits.
- Four rendered contact-sheet rows were inspected at three source positions.
  A 40-frame forward/reverse source sweep is included for visual review.

These are component and renderer checks. A live ComfyUI canvas session was not
restarted or exercised, and no performance benchmark or measured lens-video
fit is claimed. See `FLARE_ANATOMY_STUDY.md` for the reference-by-reference
observations, what was inferred, and remaining realism limitations.

Run the checks from the pack directory with the ComfyUI Python interpreter:

```powershell
<python> -m pytest -q -p no:cacheprovider
node tests/test_motion_ui.mjs
Get-Content -Raw web/flarecore_ui.js | node --input-type=module --check
Get-Content -Raw web/flarecore_motion.js | node --input-type=module --check
```

`scripts/build_adaptive_collection.py` reproduces the new presets, previews,
the browser-test contract and Python-generated curve fixtures. To inspect the
component manually, serve the pack locally and open
`tests/motion_editor_qa.html`. This workbench writes only its in-memory sample.
