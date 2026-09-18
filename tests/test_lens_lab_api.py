# SPDX-License-Identifier: Apache-2.0
import asyncio
import base64
import io
import threading

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from conftest import load_package
load_package()
from comfyui_flarecore.nodes import lens_lab_api as api


def app():
    routes = web.RouteTableDef()
    api.register_lens_routes(routes, web)
    a = web.Application()
    a.add_routes(routes)
    return a


@pytest.mark.asyncio
async def test_catalog_and_preview_route():
    async with TestClient(TestServer(app())) as client:
        response = await client.get('/flarecore/lens_lab')
        body = await response.json()
        assert response.status == 200 and len(body['pairs']) == 66
        result = await client.post('/flarecore/lens_lab/preview', json={'pair':'6:7'})
        body = await result.json()
        assert result.status == 200, body
        assert body['ray_grid'] == 48 and body['diagram']['pair'] == '6:7'
        png = base64.b64decode(body['image'].split(',', 1)[1])
        assert Image.open(io.BytesIO(png)).size == (512, 288)


@pytest.mark.asyncio
async def test_invalid_and_oversized_preview_requests():
    async with TestClient(TestServer(app())) as client:
        for raw in [{'u':8}, {'pair':'5:6'}, {'refine':1}, {'settings':{'quality':[]}}]:
            result = await client.post('/flarecore/lens_lab/preview', json=raw)
            assert result.status == 400
        result = await client.post('/flarecore/lens_lab/preview', data=b'x' * 32769)
        assert result.status == 413
        result = await client.post('/flarecore/lens_lab/preview', data=b'not json')
        assert result.status == 400


@pytest.mark.asyncio
async def test_preview_admission_is_bounded(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def fake_preview(raw):
        entered.set()
        assert release.wait(5)
        return {'finished':True}
    monkeypatch.setattr(api, 'make_preview', fake_preview)
    async with TestClient(TestServer(app())) as client:
        first = asyncio.create_task(client.post('/flarecore/lens_lab/preview', json={}))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            busy = await client.post('/flarecore/lens_lab/preview', json={})
            assert busy.status == 429
        finally:
            release.set()
        assert (await first).status == 200


def test_preview_isolation_does_not_mutate_saved_paths():
    raw = {'pair':'6:7','settings':{'disabled_pairs':['6:7']}}
    api.make_preview(raw)
    assert raw['settings']['disabled_pairs'] == ['6:7']
