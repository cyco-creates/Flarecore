# SPDX-License-Identifier: Apache-2.0
"""Temporal light tracking and keyframed light motion.

Per-frame detection is what makes flares unusable on video: a light hovering
near the threshold strobes the whole flare on and off, two lights crossing
swap identities mid-shot, and detector noise jitters the flare origin. This
module turns raw per-frame detections into stable tracks:

- greedy nearest-neighbour association carries a track id across frames
- exponential smoothing stills position and brightness noise
- new tracks fade in and lost tracks fade out over a few frames, so a
  marginal detection becomes a soft pulse instead of a strobe
- a track survives `hold` missed frames before it starts dying, riding out
  single-frame detector dropouts

Everything is plain Python floats — deterministic, device-free, testable.
"""

import math


def track_lights(detections: list[list[dict]], smoothing: float = 0.65,
                 max_jump: float = 0.12, hold: int = 3,
                 fade: int = 4) -> list[list[dict]]:
    """Stabilize per-frame detections into temporally coherent lights.

    detections: per frame, a list of {"u", "v", "brightness"} dicts (the
        output of detect.detect_lights).
    smoothing: 0 = raw positions, ->1 = heavier position/brightness EMA.
    max_jump: maximum per-frame travel (fraction of frame height) for a
        detection to continue an existing track; beyond it a new track opens.
    hold: frames a track survives unmatched at full strength-decay grace.
    fade: frames over which a track ramps in when born and out when lost.

    Returns per frame a list of {"u", "v", "brightness", "tid"} lights,
    brightness already multiplied by the fade ramp, ordered by track id so
    a light keeps its slot from frame to frame.
    """
    smoothing = min(max(smoothing, 0.0), 0.98)
    blend = 1.0 - smoothing
    fade = max(fade, 1)

    tracks: dict[int, dict] = {}
    next_tid = 0
    out: list[list[dict]] = []

    for frame in detections:
        dets = sorted(frame, key=lambda d: (-d.get("brightness", 1.0),
                                            d["v"], d["u"]))

        # greedy association: nearest pairs first, deterministic tie-break
        pairs = []
        for di, det in enumerate(dets):
            for tid, tr in tracks.items():
                dist = math.hypot(det["u"] - tr["u"], det["v"] - tr["v"])
                if dist <= max_jump:
                    pairs.append((dist, tid, di))
        pairs.sort(key=lambda p: (p[0], p[1], p[2]))

        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()
        for dist, tid, di in pairs:
            if tid in matched_tracks or di in matched_dets:
                continue
            matched_tracks.add(tid)
            matched_dets.add(di)
            tr = tracks[tid]
            det = dets[di]
            tr["u"] += (det["u"] - tr["u"]) * blend
            tr["v"] += (det["v"] - tr["v"]) * blend
            b = det.get("brightness", 1.0)
            tr["b"] += (b - tr["b"]) * blend
            tr["missed"] = 0
            tr["ramp"] = min(tr["ramp"] + 1.0 / fade, 1.0)

        born: set[int] = set()
        for di, det in enumerate(dets):
            if di in matched_dets:
                continue
            tracks[next_tid] = {
                "u": det["u"], "v": det["v"],
                "b": det.get("brightness", 1.0),
                "missed": 0, "ramp": 1.0 / fade,
            }
            born.add(next_tid)
            next_tid += 1

        dead = []
        for tid, tr in tracks.items():
            if tid in matched_tracks or tid in born:
                continue
            tr["missed"] += 1
            if tr["missed"] > hold:
                tr["ramp"] -= 1.0 / fade
                if tr["ramp"] <= 1e-6:
                    dead.append(tid)
        for tid in dead:
            del tracks[tid]

        out.append([
            {"u": tr["u"], "v": tr["v"],
             "brightness": tr["b"] * max(tr["ramp"], 0.0), "tid": tid}
            for tid, tr in sorted(tracks.items())
            if tr["ramp"] > 1e-6
        ])
    return out


def parse_keyframes(text: str) -> list[tuple[int, float, float]]:
    """Parse "frame: u,v; frame: u,v; ..." into sorted (frame, u, v) tuples.

    Whitespace-tolerant; raises ValueError naming the offending chunk.
    """
    keys = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            frame_part, _, uv_part = chunk.partition(":")
            u_str, _, v_str = uv_part.partition(",")
            keys.append((int(frame_part.strip()),
                         float(u_str.strip()), float(v_str.strip())))
        except ValueError as e:
            raise ValueError(
                f"bad keyframe {chunk!r}; expected 'frame: u,v' like '0: 0.2,0.3'"
            ) from e
    keys.sort(key=lambda k: k[0])
    frames_seen = [k[0] for k in keys]
    if len(set(frames_seen)) != len(frames_seen):
        raise ValueError("duplicate keyframe frame numbers")
    return keys


def interpolate_keyframes(text: str, frame_count: int,
                          easing: str = "smooth") -> list[tuple[float, float]]:
    """Evaluate a keyframe string over frame_count frames.

    easing 'linear' interpolates straight between keys; 'smooth' eases with
    a cosine ramp so motion starts and stops gently. Before the first key
    and after the last, the value holds.
    """
    if easing not in ("linear", "smooth"):
        raise ValueError(f"easing must be 'linear' or 'smooth', got {easing!r}")
    keys = parse_keyframes(text)
    if not keys:
        raise ValueError("no keyframes given; expected at least 'frame: u,v'")

    out = []
    for f in range(frame_count):
        if f <= keys[0][0]:
            out.append((keys[0][1], keys[0][2]))
            continue
        if f >= keys[-1][0]:
            out.append((keys[-1][1], keys[-1][2]))
            continue
        for (f0, u0, v0), (f1, u1, v1) in zip(keys, keys[1:]):
            if f0 <= f <= f1:
                t = (f - f0) / max(f1 - f0, 1)
                if easing == "smooth":
                    t = 0.5 - 0.5 * math.cos(t * math.pi)
                out.append((u0 + (u1 - u0) * t, v0 + (v1 - v0) * t))
                break
    return out
