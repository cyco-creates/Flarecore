# SPDX-License-Identifier: Apache-2.0
"""Explicit opt-in live ComfyUI test: one empty image, FlareRender, PreviewImage.

No model inference or external services; produces only ComfyUI temporary images.
Run manually: python tests/run_lens_lab_comfy_smoke.py
"""
import json
import time
import urllib.request
from pathlib import Path


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request('http://127.0.0.1:8188' + path, data=data,
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


if __name__ == '__main__':
    queue = request('/queue')
    if queue['queue_running'] or queue['queue_pending']:
        raise SystemExit('ComfyUI has work queued; smoke test not submitted.')
    info = request('/object_info/FlareRender')['FlareRender']
    inputs = {}
    for key, spec in info['input']['required'].items():
        if key == 'image':
            inputs[key] = ['1', 0]
        elif len(spec) > 1 and 'default' in spec[1]:
            inputs[key] = spec[1]['default']
        elif isinstance(spec[0], list):
            inputs[key] = spec[0][0]
        else:
            raise ValueError(f'Missing default for {key}')
    root = Path(__file__).resolve().parents[1]
    inputs.update(preset_json=(root / 'presets/lens_lab_reference.json').read_text(encoding='utf-8'),
                  position_mode='manual',light_x=.7,light_y=.3,visibility_mode='off',
                  colorspace='srgb',clamp_output=True,scene_color=0.)
    prompt = {'1':dict(class_type='EmptyImage', inputs=dict(width=512,height=288,batch_size=1,color=0)),
              '2':dict(class_type='FlareRender', inputs=inputs),
              '3':dict(class_type='PreviewImage', inputs=dict(images=['2',1]))}
    result = request('/prompt', dict(prompt=prompt))
    print('Submitted local Lens Lab smoke render:',result, flush=True)
    ident = result['prompt_id']
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        history = request('/history/' + ident).get(ident)
        if history:
            print(json.dumps(history['status'],indent=2),flush=True)
            if history['status']['status_str'] != 'success':
                raise SystemExit(1)
            print(json.dumps(history['outputs']['3'],indent=2),flush=True)
            break
        time.sleep(.5)
    else:
        raise SystemExit('Timed out waiting; check ComfyUI history before retrying.')
