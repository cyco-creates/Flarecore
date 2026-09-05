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
- a lost track COASTS on its last velocity instead of freezing: when a sun
  slides behind a pillar, the coasted position keeps moving into the
  occluder so depth occlusion completes its fade naturally, and the track
  is still in the right place to re-acquire the light on the far side —
  one continuous track instead of a cut, a dead spot, and a rebirth

Everything is plain Python floats — deterministic, device-free, testable.
"""

import math


def _smooth01(r: float) -> float:
    """Cosine-flavoured ramp: gentle at both ends of a fade."""
    r = min(max(r, 0.0), 1.0)
    return r * r * (3.0 - 2.0 * r)


def track_lights(detections: list[list[dict]], smoothing: float = 0.65,
                 max_jump: float = 0.06, hold: int = 3,
                 fade: int = 4, max_tracks: int | None = None) -> list[list[dict]]:
    """Stabilize per-frame detections into temporally coherent lights.

    detections: per frame, a list of {"u", "v", "brightness"} dicts (the
        output of detect.detect_lights). Pass MORE candidates than the
        number of flares wanted: association prefers the nearest candidate,
        so a busy frame (dappled light through trees, a row of lamps) keeps
        feeding the existing track instead of leaving it to coast while a
        rival blob elsewhere wins the frame's brightness contest.
    smoothing: 0 = raw positions, ->1 = heavier position/brightness EMA.
    max_jump: maximum per-frame travel (fraction of frame height) for a
        detection to continue an existing track; beyond it a new track opens.
        Size it to how fast the light actually MOVES between frames, not to
        how far away other lights are: it doubles as the gate that stops a
        track hopping onto a rival source. A sun in a driving shot travels
        well under 0.03 of frame height per frame, so the default is small
        on purpose. Because association measures from the track's PREDICTED
        position, a genuinely fast light still keeps its track once its
        velocity is established.
    hold: frames a track survives unmatched at full strength-decay grace.
    fade: frames over which a track ramps in when born and out when lost.
    max_tracks: cap on how many lights may exist at once. Without it every
        unmatched candidate opens a track, so two blobs trading places make
        two permanent flares that alternately brighten — which reads as the
        flare jumping from one side of frame to the other.

    Returns per frame a list of {"u", "v", "brightness", "tid"} lights,
    brightness already multiplied by the fade ramp, ordered by track id so
    a light keeps its slot from frame to frame.
    """
    # How much a candidate's size mismatch counts against it, relative to
    # distance. 1.0 means "half the energy" costs as much as being a full
    # max_jump away: enough for a bright sun to hold its track against a
    # small gap in the leaves that happens to be nearer, without stopping a
    # genuinely dimming light from being followed.
    energy_bias = 1.0

    smoothing = min(max(smoothing, 0.0), 0.98)
    blend = 1.0 - smoothing
    fade = max(fade, 1)

    tracks: dict[int, dict] = {}
    next_tid = 0
    out: list[list[dict]] = []

    for frame in detections:
        dets = sorted(frame, key=lambda d: (-d.get("brightness", 1.0),
                                            d["v"], d["u"]))

        # Greedy association, best pairs first. Two details matter when a
        # cluster of rival blobs surrounds the light (canopy gaps around a
        # sun):
        #  - distance is measured from where the track is PREDICTED to be
        #    (last observation carried forward by its velocity), not from
        #    its smoothed state. The smoothed state sits between rivals, so
        #    "nearest" flips between them and the flare wanders the cluster.
        #  - a candidate carrying much less energy than the light being
        #    followed pays for it, so a small gap does not steal the track
        #    from a big source just by being marginally closer.
        pairs = []
        for di, det in enumerate(dets):
            for tid, tr in tracks.items():
                pu = tr["out_u"] + tr["vu"]
                pv = tr["out_v"] + tr["vv"]
                dist = math.hypot(det["u"] - pu, det["v"] - pv)
                if dist > max_jump:
                    continue
                deficit = 0.0
                if tr["e"] > 0.0:
                    deficit = max(0.0, (tr["e"] - det.get("energy", 0.0)) / tr["e"])
                cost = dist / max_jump + energy_bias * deficit
                pairs.append((cost, dist, tid, di))
        pairs.sort(key=lambda p: (p[0], p[2], p[3]))

        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()
        for cost, dist, tid, di in pairs:
            if tid in matched_tracks or di in matched_dets:
                continue
            matched_tracks.add(tid)
            matched_dets.add(di)
            tr = tracks[tid]
            det = dets[di]
            tr["u"] += (det["u"] - tr["u"]) * blend
            tr["v"] += (det["v"] - tr["v"]) * blend
            # velocity from the OBSERVATIONS, so the prediction that drives
            # association is not damped by the smoothing factor
            tr["vu"] = tr["vu"] * 0.7 + (det["u"] - tr["out_u"]) * 0.3
            tr["vv"] = tr["vv"] * 0.7 + (det["v"] - tr["out_v"]) * 0.3
            b = det.get("brightness", 1.0)
            tr["b"] += (b - tr["b"]) * blend
            tr["e"] += (det.get("energy", 0.0) - tr["e"]) * blend
            tr["missed"] = 0
            tr["ramp"] = min(tr["ramp"] + 1.0 / fade, 1.0)
            # the OUTPUT position is the raw detection; the EMA above exists
            # for association and coasting velocity. Raw positions get a
            # zero-phase smooth at the end — a causal EMA would trail a
            # moving light by lag proportional to its speed
            tr["out_u"], tr["out_v"] = det["u"], det["v"]

        born: set[int] = set()
        # A track that is still matched holds its slot; only genuinely spare
        # capacity opens a new one, so the brightest rival blob cannot start
        # a competing flare while the light we are following is still visible.
        # Count every LIVE track, not just the matched ones: a track that
        # missed this frame is still on screen (holding, coasting or fading
        # out), so letting a rival be born beside it is exactly the "two
        # flares taking turns" the cap exists to prevent. A light that comes
        # back near a fading track re-matches it and revives instead.
        room = None if max_tracks is None else max(max_tracks, 1) - len(tracks)
        for di, det in enumerate(dets):
            if di in matched_dets:
                continue
            if room is not None:
                if room <= 0:
                    break
                room -= 1
            tracks[next_tid] = {
                "u": det["u"], "v": det["v"], "vu": 0.0, "vv": 0.0,
                "out_u": det["u"], "out_v": det["v"],
                "b": det.get("brightness", 1.0),
                "e": det.get("energy", 0.0),
                "missed": 0, "ramp": 1.0 / fade,
            }
            born.add(next_tid)
            next_tid += 1

        dead = []
        for tid, tr in tracks.items():
            if tid in matched_tracks or tid in born:
                continue
            tr["missed"] += 1
            # coast: keep travelling on the last velocity (damped) so an
            # occluded light stays where the light actually is
            tr["u"] += tr["vu"]
            tr["v"] += tr["vv"]
            tr["out_u"], tr["out_v"] = tr["u"], tr["v"]
            tr["vu"] *= 0.85
            tr["vv"] *= 0.85
            if tr["missed"] > hold:
                tr["ramp"] -= 1.0 / fade
                if tr["ramp"] <= 1e-6:
                    dead.append(tid)
        for tid in dead:
            del tracks[tid]

        out.append([
            {"u": tr["out_u"], "v": tr["out_v"],
             "brightness": tr["b"] * _smooth01(tr["ramp"]), "tid": tid}
            for tid, tr in sorted(tracks.items())
            if tr["ramp"] > 1e-6
        ])

    # zero-phase position smoothing per track: stills detector jitter
    # without the lag a causal filter would add
    if smoothing > 0.0:
        by_tid: dict[int, list[dict]] = {}
        for frame in out:
            for light in frame:
                by_tid.setdefault(light["tid"], []).append(light)
        for entries in by_tid.values():
            if len(entries) < 2:
                continue
            us = smooth_series([l["u"] for l in entries], smoothing)
            vs = smooth_series([l["v"] for l in entries], smoothing)
            for light, u, v in zip(entries, us, vs):
                light["u"], light["v"] = u, v
    return out


def smooth_series(values: list[float], amount: float) -> list[float]:
    """Zero-phase exponential smoothing of a scalar series (forward and
    backward passes averaged). Used to low-pass per-track occlusion along a
    clip: a light snapping behind a thin occluder becomes a fast fade
    instead of a one-frame cut, without lagging the motion."""
    n = len(values)
    if amount <= 0.0 or n < 2:
        return list(values)
    k = 1.0 - 0.9 * min(amount, 1.0)
    fwd = list(values)
    for t in range(1, n):
        fwd[t] = fwd[t - 1] * (1.0 - k) + values[t] * k
    bwd = list(values)
    for t in range(n - 2, -1, -1):
        bwd[t] = bwd[t + 1] * (1.0 - k) + values[t] * k
    return [(a + b) * 0.5 for a, b in zip(fwd, bwd)]


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
