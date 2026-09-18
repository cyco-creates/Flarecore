# SPDX-License-Identifier: Apache-2.0
"""Optional local visual QA harness; never changes a running ComfyUI workflow.

python tests/serve_lens_lab_qa.py --port 8196
Open http://127.0.0.1:8196/tests/lens_lab_qa.html
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import load_package
load_package()
from aiohttp import web
from comfyui_flarecore.nodes.lens_lab_api import register_lens_routes

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8196)
    args = parser.parse_args()
    routes = web.RouteTableDef()
    register_lens_routes(routes, web)
    app = web.Application(client_max_size=65536)
    app.add_routes(routes)
    root = Path(__file__).resolve().parents[1]
    app.router.add_static('/web/', root / 'web')
    app.router.add_static('/tests/', root / 'tests')
    web.run_app(app, host='127.0.0.1', port=args.port, print=None)
