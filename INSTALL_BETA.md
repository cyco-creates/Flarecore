# Install Flarecore

## Install

1. Copy or unzip the folder into:
   `ComfyUI/custom_nodes/comfyui-flarecore`

2. Install the requirements with the Python environment used by ComfyUI. On
   the portable Windows build:

   ```text
   python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\comfyui-flarecore\requirements.txt
   ```

3. Restart ComfyUI and hard-refresh the browser with `Ctrl+Shift+R`.

## Open the Studio

Choose **Workflow → Browse Templates → flarecore → Flarecore Studio**. The
workflow contains Flare Lab, Video Lab and Element Forge. Select the bench you
want to use, load your own image or video, and queue the workflow.

For a smaller graph, connect:

`Load Image/Video → Flarecore · Render → Combine/Video Combine`

## Optional dependencies

Rendering does not require an image-generation model. Depth estimation,
video loading and optional Krea2 or GPT Image 2 generator branches may require
additional ComfyUI nodes, models or a provider login. You can disconnect any
optional branch you do not need.

## Protect your saved work

Saved presets and custom elements are stored in this extension folder under
`presets/` and `elements/`. Back up those folders before replacing or updating
the extension.

If the editor looks incorrect after an update, save your workflow, restart
ComfyUI and hard-refresh the browser before troubleshooting further.
