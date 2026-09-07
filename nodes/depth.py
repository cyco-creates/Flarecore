# SPDX-License-Identifier: Apache-2.0
"""FlareDepthAdapter: condition a depth map for use as a flare occluder.

Takes the output of any depth source — Depth Anything, MiDaS, Zoe, Metric3D,
or a rendered Z-pass — and brings it into the range and convention FlareRender
expects. Pure tensor math; this node estimates nothing.
"""

import torch

from ..flare.depth import condition_depth, temporal_smooth_depth, NORMALIZE_MODES


class FlareDepthAdapter:
    CATEGORY = "flare"
    FUNCTION = "adapt"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("depth", "depth_mask")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "depth": ("IMAGE",),
                "normalize": (list(NORMALIZE_MODES), {"default": "per_batch"}),
                "invert": ("BOOLEAN", {"default": False}),
                "blur": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 0.25, "step": 0.001}),
                "black_point": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "white_point": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                # appended last so widgets_values in previously saved
                # workflows stay aligned
                "temporal_smooth": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "zero-phase smoothing along the batch; stills "
                               "per-frame depth-model shimmer on video",
                }),
            },
        }

    def adapt(self, depth, normalize, invert, blur, black_point, white_point,
              temporal_smooth=0.0):
        if white_point <= black_point:
            raise ValueError(
                f"white_point ({white_point}) must be greater than "
                f"black_point ({black_point})"
            )

        dtype = depth.dtype if depth.dtype.is_floating_point else torch.float32
        # Collapse to a single channel: depth maps arrive as grey RGB images.
        mono = depth[..., :3].to(dtype).mean(dim=-1)  # (B, H, W)

        out = condition_depth(
            mono, normalize=normalize, invert=invert, blur=blur,
            black_point=black_point, white_point=white_point,
        )
        out = temporal_smooth_depth(out, temporal_smooth)

        image = out.unsqueeze(-1).expand(-1, -1, -1, 3).contiguous()
        return (image, out)
