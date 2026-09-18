# SPDX-License-Identifier: Apache-2.0
"""Bounded, local-only Lens Lab previews. Final output uses the node engine."""
import asyncio
import base64
import io
import math
import time

from ..flare.lens_lab import catalog, diagram, ghost_pairs, render_lens, validate_settings


def preview_payload(raw):
    if not isinstance(raw, dict) or set(raw) - {'settings', 'u', 'v', 'pair', 'refine', 'view'}:
        raise ValueError('Invalid Lens Lab preview request')
    settings = validate_settings(raw.get('settings', {}))
    uv = []
    for key, default in [('u', .7), ('v', .3)]:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not -1 <= value <= 2:
            raise ValueError('Preview source coordinates must be finite and between -1 and 2')
        uv.append(float(value))
    pair = raw.get('pair')
    valid = {f'{a}:{b}': (a, b) for a, b in ghost_pairs()}
    if pair is not None and (not isinstance(pair, str) or pair not in valid):
        raise ValueError('Unknown reflection pair')
    refine = raw.get('refine', False)
    if type(refine) is not bool:
        raise ValueError('refine must be boolean')
    if raw.get('view', 'complete') not in ('complete', 'ghosts'):
        raise ValueError('view must be complete or ghosts')
    return settings, *uv, valid.get(pair), refine


def make_preview(raw):
    import torch
    from PIL import Image
    from ..flare.colorspace import linear_to_srgb
    settings, u, v, pair, refine = preview_payload(raw)
    start = time.perf_counter()
    drawing = diagram(settings, u, v, pair=pair)
    render_settings = dict(settings, enabled=True)
    if pair is not None:
        render_settings['disabled_pairs'] = [f'{a}:{b}' for a, b in ghost_pairs() if (a, b) != pair]
    ghosts_only = pair is not None or raw.get('view') == 'ghosts'
    if ghosts_only:
        render_settings.update(source_glow=0., source_rays=0.)
    # Reuse ComfyUI's selected device (including its CPU-only setting). The
    # single-request admission limit bounds preview work and temporary memory.
    from .render import _compute_device
    device = _compute_device(torch.device('cpu'))
    rgb = render_lens(render_settings, [dict(u=u, v=v)], 288, 512, device=device, grid_size=96 if refine else 48)
    pixels = (linear_to_srgb(rgb.clamp(0, 1)) * 255 + .5).to(torch.uint8).cpu().numpy()
    buf = io.BytesIO()
    Image.fromarray(pixels).save(buf, format='PNG', compress_level=3)
    return dict(image='data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii'),
                diagram=drawing, seconds=round(time.perf_counter() - start, 3),
                ray_grid=96 if refine else 48,
                device=str(device),
                note=('Traced ghosts only' if ghosts_only else 'Traced ghosts + artistic source finish') + ' · unit white source · sRGB preview')


def register_lens_routes(routes, web):
    # One preview worker at a time. Do not free the slot when a browser aborts
    # its request: to_thread keeps computing, so release on actual completion.
    running = set()

    @routes.get('/flarecore/lens_lab')
    async def lens_catalog(request):
        return web.json_response(catalog())

    @routes.post('/flarecore/lens_lab/preview')
    async def lens_preview(request):
        if running:
            return web.json_response({'error': 'Lens preview busy; retry shortly.'}, status=429)
        try:
            payload = await request.content.read(32769)
            # read() may return a partial chunk. Continue until EOF or the limit.
            while len(payload) <= 32768 and not request.content.at_eof():
                payload += await request.content.read(32769 - len(payload))
            if len(payload) > 32768:
                return web.json_response({'error': 'Preview request is too large.'}, status=413)
            import json
            raw = json.loads(payload)
            preview_payload(raw)
        except (ValueError, TypeError, UnicodeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        if running:
            return web.json_response({'error': 'Lens preview busy; retry shortly.'}, status=429)
        task = asyncio.create_task(asyncio.to_thread(make_preview, raw))
        running.add(task)
        def finished(done):
            running.discard(done)
            if not done.cancelled():
                done.exception()  # Retrieve an error even after a client disconnect.
        task.add_done_callback(finished)
        try:
            return web.json_response(await asyncio.shield(task))
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)
