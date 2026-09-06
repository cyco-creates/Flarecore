# FlareCore — beta install

Procedural cine lens flares for ComfyUI, plus an element forge. This is an
early build for testers; expect rough edges and please report what breaks.

## Install

1. Unzip so the folder lands at:
   `ComfyUI/custom_nodes/comfyui-flarecore`
   (the folder that contains `__init__.py`, `flare/`, `nodes/`, `web/`).
2. Install the Python deps into ComfyUI's environment. On the portable
   Windows build:
   ```
   python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\comfyui-flarecore\requirements.txt
   ```
   (torch and numpy are already there in any ComfyUI; this really just
   ensures Pillow is present.)
3. Restart ComfyUI and hard-reload the browser (Ctrl+Shift+R) so the new
   frontend loads.

## Where to start

- **Workflow → Browse Templates → flarecore → Flarecore Studio.** Three
  benches in one graph — element forge, flare lab, video lab — with a
  switch on the left; click a bench and the others mute.
- The Flare Render node carries its own editor: a point picker and a stack
  editor. Drop a **Flare Preset Loader**, pick a look, and the editor fills
  in. Presets are grouped Anamorphic / Spherical / Scenario / Utility.
- **flarecore_trigger_lab** is a self-contained demo — nothing to load,
  just Queue it.

## The element forge

The forge makes new flare textures with an image model and files them in
the library. It carries **two generators with a manual switch** (Flare
Generator Select): `a` = the local **Krea2** chain, `b` = **GPT Image 2**.
Only the selected one runs. GPT Image 2 is an OpenAI API node and needs you
signed in to the Comfy API in your browser; the local Krea2 chain needs the
Krea2 models. If you have neither, the ~200 shipped element textures still
cover every family.

## Known alpha limitations

- Saved presets and forged elements are written **inside this folder**
  (`presets/`, `elements/`). If you reinstall by replacing the folder,
  copy those two out first — they are not yet stored in ComfyUI's user
  directory.
- The GPT Image 2 branch only renders when you are logged in to the Comfy
  API; there is no headless/API-key path yet.
- Requires a recent ComfyUI frontend (subgraphs, DOM widgets). If the
  editor panels look wrong, hard-reload before reporting.

## Reporting

Tell us the ComfyUI version, the preset or element involved, and whether a
hard reload changed anything — a stale cached frontend is the most common
false alarm.
