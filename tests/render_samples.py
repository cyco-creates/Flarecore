# SPDX-License-Identifier: Apache-2.0
"""Render every shipped preset to PNGs for visual inspection.

Usage:
    python tests/render_samples.py [--photo PATH] [--device cpu|cuda]

For each preset in presets/: renders over a black frame and over a test photo
(synthesized if --photo is not given), at 1024x576 and 512x512, single-frame
and as a 4-frame batch (the light moves per batch frame). Also renders a
12-frame light sweep and a performance timing.

Output: tests/out/samples/, tests/out/sweep/
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402


def _load_package():
    if "comfyui_flarecore" in sys.modules:
        return sys.modules["comfyui_flarecore"]
    spec = importlib.util.spec_from_file_location(
        "comfyui_flarecore", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["comfyui_flarecore"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


PKG = _load_package()
FlareRender = PKG.NODE_CLASS_MAPPINGS["FlareRender"]
FlarePresetLoader = PKG.NODE_CLASS_MAPPINGS["FlarePresetLoader"]

OUT = Path(__file__).parent / "out"


def synth_photo(height, width, device):
    """Deterministic synthetic test photo: sky gradient, horizon, shapes."""
    v = torch.linspace(0, 1, height, device=device).unsqueeze(1).expand(height, width)
    u = torch.linspace(0, 1, width, device=device).unsqueeze(0).expand(height, width)
    img = torch.zeros(height, width, 3, device=device)
    # sky: blue fading to warm at the horizon (at v = 0.62)
    sky = (v / 0.62).clamp(0, 1)
    img[..., 0] = 0.25 + 0.45 * sky
    img[..., 1] = 0.4 + 0.25 * sky
    img[..., 2] = 0.75 - 0.15 * sky
    # ground
    ground = v > 0.62
    img[ground] = torch.tensor([0.16, 0.14, 0.12], device=device)
    # a few dark buildings on the horizon
    for i, (x0, x1, top) in enumerate([(0.1, 0.18, 0.45), (0.55, 0.6, 0.5),
                                       (0.8, 0.92, 0.38)]):
        mask = (u > x0) & (u < x1) & (v > top) & (v < 0.63)
        img[mask] = torch.tensor([0.08, 0.08, 0.1], device=device)
    # mild vignette
    r2 = (u - 0.5) ** 2 * 2.2 + (v - 0.5) ** 2
    img *= (1.0 - 0.35 * r2).clamp(0.3, 1.0).unsqueeze(-1)
    return img.clamp(0, 1)


def save_png(tensor_hw3, path):
    arr = (tensor_hw3.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)


def run(preset_json, image, light=(0.3, 0.35), **overrides):
    args = dict(
        preset_json=preset_json, position_mode="manual",
        light_x=light[0], light_y=light[1],
        detect_threshold=0.8, detect_max_lights=1, occlusion_radius=0.02,
        invert_depth=False, intensity=1.0, scale=1.0, blend_mode="add",
        clamp_output=True, seed=0,
    )
    args.update(overrides)
    return FlareRender().render(image=image, depth=None, **args)


def sample_renders(photo_path, device):
    loader = FlarePresetLoader()
    files = FlarePresetLoader.INPUT_TYPES()["required"]["preset_file"][0]
    sizes = [(576, 1024), (512, 512)]

    for name in files:
        (text,) = loader.load(name)
        stem = Path(name).stem
        for height, width in sizes:
            if photo_path:
                pil = Image.open(photo_path).convert("RGB").resize((width, height))
                photo = torch.from_numpy(
                    np.asarray(pil).astype(np.float32) / 255.0
                ).to(device)
            else:
                photo = synth_photo(height, width, device)
            black = torch.zeros(height, width, 3, device=device)

            for base_name, base in (("black", black), ("photo", photo)):
                # single frame
                img = base.unsqueeze(0)
                out, _, _ = run(text, img)
                save_png(out[0], OUT / "samples" /
                         f"{stem}_{width}x{height}_{base_name}.png")
                # 4-frame batch, light moving per frame
                batch = base.unsqueeze(0).repeat(4, 1, 1, 1)
                outs = []
                for i in range(4):
                    lx = 0.2 + 0.2 * i
                    o, _, _ = run(text, batch[i:i + 1], light=(lx, 0.35))
                    outs.append(o[0])
                strip = torch.cat(outs, dim=1)
                save_png(strip, OUT / "samples" /
                         f"{stem}_{width}x{height}_{base_name}_batch4.png")
        print(f"rendered {stem}")


def sweep(device, preset_name="clean_35mm.json", frames=12):
    """12-frame light sweep to verify the ghost chain tracks the axis."""
    (text,) = FlarePresetLoader().load(preset_name)
    height, width = 288, 512
    base = torch.zeros(1, height, width, 3, device=device)
    tiles = []
    for i in range(frames):
        f = i / (frames - 1)
        lx, ly = 0.1 + 0.8 * f, 0.2 + 0.6 * f
        out, _, _ = run(text, base, light=(lx, ly))
        tiles.append(out[0])
        save_png(out[0], OUT / "sweep" / f"{Path(preset_name).stem}_{i:02d}.png")
    rows = [torch.cat(tiles[r * 4:(r + 1) * 4], dim=1) for r in range(3)]
    save_png(torch.cat(rows, dim=0),
             OUT / "sweep" / f"_sweep_sheet_{Path(preset_name).stem}.png")
    print(f"sweep rendered ({frames} frames)")


def perf(device):
    """Time a 15-element render at 1024x576 (definition of done #7)."""
    spec = json.loads((ROOT / "presets" / "specimen_all_elements.json").read_text())
    elements = spec["elements"]
    while len(elements) < 15:
        elements.append(dict(elements[len(elements) % 7]))
    text = json.dumps(spec)
    img = torch.zeros(1, 576, 1024, 3, device=device)

    run(text, img)  # warmup / compile caches
    if device == "cuda":
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    n = 10
    for _ in range(n):
        run(text, img)
    if device == "cuda":
        torch.cuda.synchronize()
    ms = (time.perf_counter() - t0) / n * 1000
    print(f"perf: 15-element 1024x576 on {device}: {ms:.1f} ms/frame")
    return ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", default=None)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()
    print(f"device: {args.device}")
    sample_renders(args.photo, args.device)
    sweep(args.device)
    sweep(args.device, "specimen_all_elements.json")
    perf(args.device)


if __name__ == "__main__":
    main()
