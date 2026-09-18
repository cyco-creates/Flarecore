# Flarecore V2 — 0.2.0 beta 1

V2 adds an experimental physical lens tracer, two new preset collections built from
lens-flare reference study, and new appearance controls in the artistic renderer.

Existing work is unaffected. Presets without a `lens_lab` object render exactly as
they did in 0.1.2, node IDs, inputs and outputs are unchanged, and every new
parameter defaults to its previous behaviour.

## Lens Lab — experimental spherical optics

**Lens Lab** is a mode of Flarecore · Render, not a separate application and not an
image-generation model. It traces spherical refractions and two-reflection ghosts
through a reference prescription, inside ComfyUI.

- **Simple** opens first: pick Balanced, Cool, Warm or Uncoated, then adjust
  reflection brightness, light glow, light rays and aperture.
- **Advanced** groups aperture, coating, source finish, sensor/exposure/quality,
  per-surface and per-path controls.
- **Inside the lens** draws a glass cutaway with the amber aperture and green
  sensor. Click glass to open its inspector; isolate a reflection path to see that
  ghost alone and follow its ray bounces.
- **Output** has three modes: lens only, lens plus your elements (hybrid), or your
  artistic stack alone with the lens settings retained rather than deleted.
- Spectral single-layer coating studies and per-surface reflection suppression.
- Every setting carries an **i** tag explaining its units, effect and limits on
  hover or keyboard focus.

Edits are transactional. Cancel or Escape discards them, Apply is a single editor
undo step, and Apply refuses to overwrite a preset that changed while the dialog
was open. A preview that is still updating is dimmed and labelled outdated, and
Apply stays unavailable until it finishes: an old image is never presented as the
current result.

The dialog's preview source is deliberately separate from your tracked source.
Dragging it explores the look without changing or baking tracker data.

**This is an experimental constant-index prototype.** It is not measured commercial
lens data, and it does not model physical diffraction, anamorphic optics or zoom.

See [docs/LENS_LAB.md](docs/LENS_LAB.md).

## Two new preset collections — 41 presets to 74

**Lens inspired** (15) — artistic studies named after the commercial lens families
that informed them, on Modern, Character and Anamorphic shelves: ARRI Signature
Prime and Master Prime, ZEISS Supreme Radiance, Cooke S8-i FF and Panchro-i
Classic, Panavision Primo, C Series and SPF, Leitz HUGO, Canon Sumire, Atlas Orion
and Mercury, Hawk V-Lite Vintage 74, Cooke and ARRI anamorphics.

**Reference studies** (15, `study_*.json`) — built against 153 paired reference
screenshots spanning 15 lens families and 34 focal/stop conditions, plus 58 frames
from a filmed flare demonstration. Each is documented with its reference condition
and the specific ways it still differs from the footage.

Both collections are self-contained procedural stacks: no texture downloads, no
image generation, no API key, no paid credits. They intentionally differ in
strength, because a high-contrast modern lens should not flare like an
enhanced-flare one.

**These are authored interpretations, not measured replicas or recovered optical
prescriptions.** Brand names identify the reference only; no endorsement or
affiliation is implied. No reference imagery or commercial test footage ships with
the extension.

See [docs/LENS_INSPIRED_PRESETS.md](docs/LENS_INSPIRED_PRESETS.md) and
[docs/REFERENCE_LENS_STUDIES.md](docs/REFERENCE_LENS_STUDIES.md).

## Artistic renderer — new appearance controls

All default to the previous output, so existing presets are untouched.

- **Density bias** for iris and spectral pupils: negative concentrates fill toward
  the centre, positive favours the edge.
- **Surface detail**: seeded, filtered density variation that fades below the local
  pixel footprint. Procedural variation, not a scan of a coating.
- **Ray falloff, fan and ray taper**: independent longitudinal decay, widening and
  near-source concentration.
- **Scatter** on glows; **pupil shear**, **caustic fold** and **trefoil** on iris
  and spectral shapes; **edge bias** on rings.
- Three new optical-response targets: pupil shear, caustic fold and visible arc.
- 11 new library elements (182 to 193), including airy cores, wide annuli, wobbly
  hoops and a cyan hairline streak.

## Fix — image visibility no longer extinguishes extended sources

When the measuring annulus around a source contained part of the emitter itself —
an extended lamp rather than dark background — the background estimate could
subtract almost the entire reference, turning a small flux loss into complete
extinction of the flare.

Subtraction is now bounded by the measured source-to-surround contrast, with a
continuous conservative fallback when source and surroundings are genuinely alike.

This cannot infer an emitter's size. Set the source radius to cover the whole
luminous body: at 1080p, a radius of 0.02 is about 21.6 pixels.

## Editor

- Contextual **i** help tags throughout the node and the Lens Lab dialog, reachable
  by keyboard, dismissed with Escape.
- The toolbar reports the renderer that is actually active — artistic, hybrid, or
  the Lens Lab coating — instead of leaving a stale preset name highlighted.
- In physical-only mode the node shows lens controls in place of inactive element
  rows, and both switches participate in undo/redo.

## Documentation

New: Lens Lab guide, the two collection and research notes with machine-readable
provenance, a technical report on architecture, and a record of design decisions
that deviate from the original build spec.

## Verification

949 Python tests and 8 browser/UI suites pass.

These establish correct, deterministic operation and inspectable results. They are
not a measure of similarity to any commercial lens, nor a guarantee of tracking
quality on every shot.

## Limits

Lens Lab is a constant-index prototype without diffraction, anamorphic optics or
zoom. The preset studies are authored interpretations and are not calibrated
against commercial optics. Source tracking through complete obstruction remains an
estimate, and image visibility still needs a clear reference in the clip and a
source radius that covers the emitter.

## Installing

1. Back up your `presets/` and `elements/` folders — they can contain your own work.
2. Extract into `ComfyUI/custom_nodes/comfyui-flarecore` and install
   `requirements.txt` with ComfyUI's Python environment.
3. Restart ComfyUI and hard-refresh the browser with `Ctrl+Shift+R`.
4. Open **Workflow → Browse Templates → flarecore → Flarecore Studio**.

Report problems with this version, your ComfyUI version, the selected source mode
and a minimal workflow. Remove credentials and private media first.

---

The previous release, 0.1.2 beta 1, introduced independent flare groups,
the pinned preset gallery, reworked bright-source tracking and image-based
source visibility. Those remain unchanged here.
