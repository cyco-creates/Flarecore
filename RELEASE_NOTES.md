# Flarecore 0.1.1 beta 1

## For testers

1. Back up your existing `presets/` and `elements/` folders before updating; they can contain your own saved content.
2. Extract `comfyui-flarecore` into `ComfyUI/custom_nodes/` and install `requirements.txt` using ComfyUI's Python environment.
3. Restart ComfyUI and refresh its browser page.
4. Open the Studio workflow. Select your own image or video; source media is not bundled.

The nodes are grouped under **Flarecore**. Start with **Flarecore · Render** or **Flarecore · Element Forge**.

## Included improvements

- Six primary light-source jobs with grouped tracking methods and explicit recommended settings.
- Optical-response curves with endpoint labels, dashed held-value tails and bounded dragging.
- Grouped element controls and a redesigned Element Forge prompt panel.
- Restored depth in Flare Lab and rewritten workflow guides.
- Adaptive presets, realism elements and renderer precision fixes.
- Existing node IDs and older trigger-based presets remain compatible.

## Dependencies and limits

The renderer itself needs no image-generation service. The Studio's optional video, depth, switching and generation nodes may require additional custom-node packages and model files. In particular, the template uses VideoHelperSuite, Depth Anything V2 from comfyui_controlnet_aux, and a Pixaroma group switch. The Krea2 subgraph needs its models; the hosted generator requires its provider's sign-in and may incur charges. Disconnect or replace optional branches you do not use.

The old trigger editor is retired; existing rules still render and can be explicitly removed. This beta has automated renderer/contract tests and isolated browser UI tests; it is not a guarantee of tracking quality on every shot or compatibility with every third-party extension.

When reporting a problem, include this version, ComfyUI version, selected source mode and a minimal workflow. Remove credentials and private media before sharing reports.
