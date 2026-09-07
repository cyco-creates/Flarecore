# FlareCore documentation

## Using the studio

- [Studio guide](STUDIO_GUIDE.md): where to start, the editor sections, depth, and outputs.
- [Optical response](OPTICAL_RESPONSE.md): moving-light curves and their limits.
- [Realism collection](REALISM_UPGRADE.md): added elements and presets.
- [Flare anatomy study](FLARE_ANATOMY_STUDY.md): visual-reference analysis; observed features versus inferred behavior.
- [Installation](../INSTALL_BETA.md): setup and dependencies.

## Developing the system

- `flare/`: schema, procedural elements, transforms, optical response, and rendering.
- `nodes/`: ComfyUI inputs, outputs, and local API routes.
- `web/flarecore_ui.js`: studio/editor integration and shared editor styling.
- `web/flarecore_motion.js`: response editor and curve interaction.
- `presets/`, `elements/`, `prompts/`: user-facing content libraries. Keep existing filenames stable so saved presets continue to load.
- `example_workflows/`: Studio and trigger demo workflows.
- `scripts/`: reproducible collection generation and targeted workflow maintenance.
- `tests/`: renderer/contract regressions and browser workbenches. `editor_layout_qa.html` exercises the actual advanced-editor builder with its host CSS; `motion_editor_qa.html` isolates the curve editor.

Preview folders contain generated examples and test fixtures, not runtime library assets. Local planning notes, dated development audits, and one-off migration scripts are not distributed.
