// SPDX-License-Identifier: Apache-2.0
// flarecore: the FlareRender interface — a point picker for the light and
// flare anchor, and a stack editor over preset_json with an element gallery.
//
// The editor IS the interface: every node widget stays hidden until the ⚙
// button reveals them. Both panels live in one extension so a single owner
// controls widget order and node height.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { createMotionPanel } from "./flarecore_motion.js";
import { openPresetGallery } from "./flarecore_presets.js";
import { SOURCE_FIELDS, clone, sceneDocument, activeGroup, sourceValue, setGroupSource, ensureGroups, newGroupId, selectGroupResult } from "./flarecore_groups.js";

/* ------------------------------------------------------------------ utils */

// The two DOM panels. They must stay at the end of node.widgets: ComfyUI
// writes a null into widgets_values for each of them instead of omitting
// them, so any other position shifts every real value on reload.
const PANEL_WIDGETS = new Set(["flare_layout", "flare_editor"]);

// Everything else on the node hides behind the ⚙ button — the picker and
// the editor are the interface; the widgets are the escape hatch.
// The two DOM panels ARE the interface and always show; everything else is
// the escape hatch. Listing what stays rather than what hides means an input
// appended later is hidden automatically instead of sitting on the node face
// until someone remembers to add it here.
function nodeWidgets(node) {
  return (node.widgets || []).filter((w) => !PANEL_WIDGETS.has(w.name));
}

// `widget.hidden` is what the current frontend's layout and draw both read.
// The old idiom of assigning type = "hidden" is inert here — measured on a
// stock KSampler, the node height ignored it (262 -> 262) while the hidden
// flag shrank it (262 -> 238) — which is why suppressed widgets were still
// being painted over the panels. Both are set: the flag for this frontend,
// the type for older ones.
function hideWidget(w) {
  if (w._fcHidden) return;
  w._fcHidden = true;
  w._fcType = w.type;
  w._fcCompute = w.computeSize;
  w.hidden = true;
  w.type = "hidden";
  w.computeSize = () => [0, -4];
  if (w.element?.style) w.element.style.display = "none";
}

function showWidget(w) {
  if (!w._fcHidden) return;
  w._fcHidden = false;
  w.hidden = false;
  w.type = w._fcType;
  if (w._fcCompute) w.computeSize = w._fcCompute;
  else delete w.computeSize;
  if (w.element?.style) w.element.style.display = "";
}

// The editor fills whatever node height remains below it. Its y-offset
// inside the node is measured by the layout every draw, so height =
// node height - offset - margin tracks manual resizes exactly.
function fitEditor(node) {
  const ew = findWidget(node, "flare_editor");
  if (!ew) return;
  const y = Number.isFinite(ew.y) && ew.y > 0 ? ew.y : ew.last_y;
  if (!Number.isFinite(y) || y <= 0) return;
  node._fcEditorH = Math.max(280, node.size[1] - y - 12);
}

function setAdvanced(node, visible) {
  node._fcAdvanced = visible;
  // properties are serialized with the workflow, so the toggle survives
  // save/load — a plain JS field would silently reset to closed
  if (node.properties) node.properties.fc_advanced = visible;
  for (const w of nodeWidgets(node)) {
    (visible && !(activeGroup(sceneDocument(node)) && SOURCE_FIELDS.has(w.name)) ? showWidget : hideWidget)(w);
  }
  // grow when the widgets need more room, but never shrink a node the user
  // deliberately made taller
  const want = node.computeSize()[1];
  if (node.size[1] < want) node.setSize([node.size[0], want]);
  node.setDirtyCanvas(true, true);
  // the editor's y-offset changes when widgets appear/disappear; refit
  // once the next layout pass has measured it
  setTimeout(() => { fitEditor(node); node.setDirtyCanvas(true, true); }, 80);
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}
function getVal(node, name, fallback) {
  const grouped=sourceValue(node,name);if(grouped!==undefined)return Number(grouped);
  const w = findWidget(node, name);
  return w ? Number(w.value) : fallback;
}
function setVal(node, name, value) {
  if(setGroupSource(node,name,value))return;
  const w = findWidget(node, name);
  if (w) {
    w.value = typeof value === 'number' ? Math.round(value * 1000) / 1000 : value;
    w.callback?.(w.value, app.canvas, node, null, null);
  }
}

// Rendered solo previews, keyed by the element's content so hovering the
// same row twice costs one render, not two.
const previewCache = new Map();
async function elementPreviewUrl(elem, global) {
  const key = JSON.stringify([elem, global]);
  if (previewCache.has(key)) return previewCache.get(key);
  const res = await fetch(api.apiURL("/flarecore/element_preview"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ element: elem, global }),
  });
  if (!res.ok) throw new Error(`preview ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  if (previewCache.size > 200) {
    const first = previewCache.keys().next().value;
    URL.revokeObjectURL(previewCache.get(first));
    previewCache.delete(first);
  }
  previewCache.set(key, url);
  return url;
}

function elementThumbUrl(ref) {
  return api.apiURL(`/flarecore/element/${ref.split("/").map(encodeURIComponent).join("/")}`);
}

// Where the light comes from. Each mode shows only its own controls, so the
// panel says what this flare is actually doing instead of listing every
// knob the node owns.
const POSITION_MODES = [
  ["manual", "Place light"],
  ["detect", "Detect bright sources"],
  ["track", "Track bright sources"],
  ["follow", "Follow camera motion"],
  ["point_track", "Track a chosen feature"],
  ["path", "Draw / edit a path"],
];

const sourceFamily = mode => mode === "detect_with_manual_offset" ? "detect"
  : ["lock", "track_dots"].includes(mode) ? "track" : mode;

// Starting values are explicit opt-in via Recommended settings. Switching
// source jobs must not silently overwrite an artist's existing tuning.
const MODE_DEFAULTS = {
  manual: {},
  detect: { detect_threshold: 0.6, detect_max_lights: 1, track_smoothing: 0.85,
            light_travel: 1, scene_lock: 1, search_radius: 0 },
  detect_with_manual_offset: { detect_threshold: 0.6, detect_max_lights: 1,
            track_smoothing: 0.85, light_travel: 1, scene_lock: 1, search_radius: 0 },
  track: { detect_threshold: 0.6, detect_max_lights: 1, track_smoothing: 0.85,
           track_max_jump: 0.1, light_travel: 1, track_hold: 3, track_fade: 4,
           search_radius: 0, scene_lock: 0 },
  track_dots: { detect_threshold: 0.5, detect_max_lights: 4, track_smoothing: 0.7,
                track_max_jump: 0.08, light_travel: 1, track_hold: 3, track_fade: 4,
                search_radius: 0, scene_lock: 0 },
  lock: { detect_threshold: 0.6, detect_max_lights: 1, track_smoothing: 0.9,
          track_max_jump: 0.06, light_travel: 1, scene_lock: 0, visibility_mode: "hybrid" },
  point_track: { track_feature: 32, track_search: 64, track_smoothing: 0.6,
                 light_travel: 1 },
  follow: { track_smoothing: 0.6, light_travel: 1 },
  path: { light_travel: 1 },
};

const MODE_HINT = {
  manual: "drag the light and the anchor on the picker above.",
  detect: "the brightest REGION in each frame, then held to the camera's "
    + "motion by scene lock, so a frame that hops to a rival source cannot "
    + "drag the light. For a still, or a clip with one clean source.",
  detect_with_manual_offset:
    "detection, shifted by how far the picker's light sits from centre.",
  track: "matches bright-source detections across frames. Max jump is how far the light may "
    + "travel per frame: lower it if the flare wanders between nearby "
    + "lights, raise it if the flare duplicates or drops out on fast moves.",
  track_dots: "white dots on a dark plate, one flare each, each keeping its "
    + "identity. The threshold is relative to the brightest dot in the clip. "
    + "Max lights is a cap, not a quota — a higher cap never invents flares.",
  lock: "recommended for a sun behind branches: tracks the luminous core, "
    + "bridges hidden intervals and measures visibility separately. Place the light "
    + "near your target and narrow search radius if there are rival highlights. "
    + "Hidden positions are estimates; inspect the path before baking it.",
  follow: "place the source, even outside the frame. Scene motion carries it "
    + "through the clip without light detection. Needs trackable detail in the scene.",
  point_track: "place one tracker on a feature and the light follows it; add "
    + "a second and it becomes the anchor, so the flare axis takes their "
    + "rotation and scale too. Pick something with contrast — a blown "
    + "highlight has no detail to match, so track a nearby edge instead.",
  path: "click the picker to drop a point, drag to move one, shift-click to "
    + "remove. The light travels the whole path across the clip.",
};

// Which widget the picker's click-to-place tool edits, and how many points
// it accepts. Path mode draws a route; point_track places one or two
// trackers on features. Same gesture, different destination.
function pointTool(node) {
  const mode = getStr(node, "position_mode") || "manual";
  if (mode === "path") return { widget: "light_path", max: Infinity };
  if (mode === "point_track") return { widget: "track_points", max: 2 };
  return null;
}

// Fraction of the picker left free around the frame on each side, so a
// light can be dragged out of the picture.
const PICKER_MARGIN = 0.11;
const UV_MIN = -1, UV_MAX = 2;   // matches the node's light/anchor ranges

// Catmull-Rom, the same curve flare/track.py samples the light along.
// test_path_reference_points pins values both must produce.
function catmullRom(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  const f = (a, b, c, d) => 0.5 * ((2 * b) + (-a + c) * t
    + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
  return [f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])];
}

function samplePath(pts, n) {
  if (!pts.length) return [];
  if (pts.length === 1 || n <= 1) return new Array(Math.max(n, 1)).fill(pts[0]);
  const ext = [pts[0], ...pts, pts[pts.length - 1]];
  const segments = pts.length - 1;
  const out = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * segments;
    const k = Math.min(Math.floor(x), segments - 1);
    out.push(catmullRom(ext[k], ext[k + 1], ext[k + 2], ext[k + 3], x - k));
  }
  return out;
}

function parsePath(text) {
  return String(text || "").split(";").map((c) => c.trim()).filter(Boolean)
    .map((c) => c.split(",").map((n) => parseFloat(n)))
    .filter((p) => p.length === 2 && p.every(Number.isFinite));
}

function formatPath(pts) {
  return pts.map((p) => `${p[0].toFixed(4)},${p[1].toFixed(4)}`).join("; ");
}

function getStr(node, name) {
  const grouped=sourceValue(node,name);if(grouped!==undefined)return String(grouped);
  const w = findWidget(node, name);
  return w ? String(w.value ?? "") : "";
}

function setStr(node, name, value) {
  if(setGroupSource(node,name,value))return;
  const w = findWidget(node, name);
  if (w) w.value = value;
}

/* ----------------------------------------------------------- point picker */

class PointPicker {
  constructor(node) {
    this.node = node;
    this.backdrop = null;
    this.drag = null;
    this.pathMode = false;
    this.dragPoint = -1;

    this.el = document.createElement("div");
    this.el.style.cssText =
      "width:100%;height:100%;background:#0d0d11;border:1px solid #303038;" +
      "border-radius:8px;overflow:hidden;box-sizing:border-box;";
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText =
      "width:100%;height:100%;display:block;cursor:crosshair;";
    this.el.appendChild(this.canvas);

    for (const t of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {
      this.canvas.addEventListener(t, (e) => this.onPointer(e));
    }
    if (typeof ResizeObserver !== "undefined") {
      this._ro = new ResizeObserver(() => this.draw());
      this._ro.observe(this.el);
    }
    // A detached element only means the widget is momentarily unmounted
    // (other tab, collapsed node) — skip the tick, don't self-destruct.
    // Real teardown happens in destroy() from the node's onRemoved.
    this._poll = setInterval(() => {
      if (!document.body.contains(this.el)) return;
      const sig = ["light_x", "light_y", "flare_x", "flare_y"]
        .map((n) => getVal(this.node, n, 0)).join("|");
      if (sig !== this._sig) { this._sig = sig; this.draw(); }
    }, 300);
  }

  destroy() {
    clearInterval(this._poll);
    this._ro?.disconnect();
  }

  // CSS-pixel size of the panel. clientWidth/Height are layout values, so
  // they stay put under the canvas zoom transform; getBoundingClientRect
  // would shrink with zoom and rescale the backing store on every pan.
  get cssSize() {
    return [this.el.clientWidth, this.el.clientHeight];
  }

  // The frame sits inset in the picker so a light can be placed OUTSIDE
  // it: on a great many shots the sun is above the top edge, and a picker
  // that cannot say so forces the light to the wrong place.
  imageRect() {
    const [W, H] = this.cssSize;
    const aspect = this.backdrop
      ? this.backdrop.width / this.backdrop.height : 16 / 9;
    const M = PICKER_MARGIN;
    let w = W * (1 - 2 * M), h = w / aspect;
    if (h > H * (1 - 2 * M)) { h = H * (1 - 2 * M); w = h * aspect; }
    return { x: (W - w) / 2, y: (H - h) / 2, w, h };
  }

  point(nx, ny, r) {
    return [r.x + getVal(this.node, nx, 0.5) * r.w,
            r.y + getVal(this.node, ny, 0.5) * r.h];
  }

  draw() {
    const [W, H] = this.cssSize;
    if (W < 8 || H < 8) return;
    const dpr = window.devicePixelRatio || 1;
    const bw = Math.round(W * dpr), bh = Math.round(H * dpr);
    if (this.canvas.width !== bw || this.canvas.height !== bh) {
      this.canvas.width = bw;
      this.canvas.height = bh;
    }
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#0d0d11";
    ctx.fillRect(0, 0, W, H);

    const r = this.imageRect();
    ctx.fillStyle = "#101014";
    ctx.fillRect(0, 0, W, H);
    if (this.backdrop) {
      ctx.drawImage(this.backdrop, r.x, r.y, r.w, r.h);
      ctx.strokeStyle = "rgba(255,255,255,0.28)";
      ctx.lineWidth = 1;
      ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
    } else {
      ctx.fillStyle = "#5a5a66";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("render once for a backdrop — the points already work",
        W / 2, H / 2);
      ctx.textAlign = "left";
    }

    const [lx, ly] = this.point("light_x", "light_y", r);
    const [fx, fy] = this.point("flare_x", "flare_y", r);

    // trigger preview: the region where the rule fires, painted over the
    // plate exactly as the engine evaluates it (see triggerFactor)
    const trig = this.node._fcTriggerPreview;
    if (trig) {
      const aspect = this.backdrop
        ? this.backdrop.width / this.backdrop.height : 16 / 9;
      const gx = (getVal(this.node, "light_x", 0.5) - 0.5) * 2 * aspect;
      const gy = (getVal(this.node, "light_y", 0.5) - 0.5) * 2;
      const step = 4;                       // CSS px per sample cell
      ctx.save();
      ctx.beginPath();
      ctx.rect(r.x, r.y, r.w, r.h);
      ctx.clip();
      for (let py = 0; py < r.h; py += step) {
        for (let px = 0; px < r.w; px += step) {
          const u = (px + step / 2) / r.w, v = (py + step / 2) / r.h;
          const f = triggerFactor(trig, (u - 0.5) * 2 * aspect, (v - 0.5) * 2,
            aspect, gx, gy);
          if (f <= 0.01) continue;
          ctx.fillStyle = `rgba(232,72,72,${(0.55 * f).toFixed(3)})`;
          ctx.fillRect(r.x + px, r.y + py, step, step);
        }
      }
      ctx.restore();
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.font = "10px sans-serif";
      ctx.fillText(`trigger preview — ${trig.mode}`, r.x + 6, r.y + 13);
    }

    ctx.lineWidth = 1.4;
    ctx.strokeStyle = "rgba(255,255,255,0.6)";
    ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(fx, fy); ctx.stroke();
    ctx.setLineDash([5, 5]);
    ctx.strokeStyle = "rgba(255,255,255,0.28)";
    ctx.beginPath(); ctx.moveTo(fx, fy);
    ctx.lineTo(fx + (fx - lx), fy + (fy - ly)); ctx.stroke();
    ctx.setLineDash([]);
    for (let i = 1; i < 4; i++) {
      const t = i / 4;
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.beginPath();
      ctx.arc(lx + (fx - lx) * t, ly + (fy - ly) * t, 1.7, 0, Math.PI * 2);
      ctx.fill();
    }

    // What the last render did with the light: the solved path, drawn so a
    // track can be judged before it is baked; and the search region when
    // one is set, drawn around the light point it is centred on.
    {
      const modeNow = getStr(this.node, "position_mode") || "manual";
      const trk = this.node._fcTrack;
      if (Array.isArray(trk) && modeNow !== "manual" && modeNow !== "path") {
        const P = (u, v) => [r.x + u * r.w, r.y + v * r.h];
        const pts = trk.filter((q) => Array.isArray(q));
        if (pts.length > 1) {
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = "rgba(255,180,84,0.8)";
          ctx.beginPath();
          pts.forEach((q, i) => {
            const [sx, sy] = P(q[0], q[1]);
            i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
          });
          ctx.stroke();
          const aps = pts.filter((q) => q[2] != null);
          if (aps.length > 1) {
            ctx.strokeStyle = "rgba(102,217,255,0.65)";
            ctx.beginPath();
            aps.forEach((q, i) => {
              const [sx, sy] = P(q[2], q[3]);
              i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
            });
            ctx.stroke();
          }
        }
      }
      const sr = getVal(this.node, "search_radius", 0);
      if (sr > 0 && ["detect", "detect_with_manual_offset", "track", "track_dots", "lock"].includes(modeNow)) {
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = "rgba(102,217,255,0.85)";
        ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.arc(lx, ly, sr * r.h, 0, Math.PI * 2); ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // The lights input (tracking, keyframes) overrides light_x/light_y
    // entirely. Say so instead of leaving a handle that moves nothing: the
    // last render reports what actually placed the light.
    const posMode = getStr(this.node, "position_mode") || "manual";
    const li = this.node.inputs?.find((i) => i.name === "lights");
    const linked = !!(li && li.link != null);
    const driven = linked || posMode !== "manual";
    const lightSrc = linked ? "lights input" : posMode;
    const searchOn = !linked && ["detect", "detect_with_manual_offset", "track", "track_dots", "lock"].includes(posMode)
      && getVal(this.node, "search_radius", 0) > 0;
    const followOn = !linked && posMode === "follow";
    // a baked two-tracker solve carries the anchor on a path of its own
    const anchorDriven = !linked && posMode === "path"
      && parsePath(getStr(this.node, "anchor_path")).length > 1;
    if (driven) {
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(r.x, r.y + r.h - 17, r.w, 17);
      ctx.fillStyle = "#ffc98a";
      ctx.font = "10px sans-serif";
      ctx.fillText(
        linked
          ? "light driven by the lights input — disconnect it to use the source controls"
          : (posMode === "path"
            ? (anchorDriven
              ? "light and anchor follow their drawn paths"
              : "light follows the drawn path — the anchor still drags")
            : followOn
              ? "drag the light to where the source really is, even outside the frame — the camera carries it"
              : searchOn
                ? "searching inside the ring — drag the light point to move it"
                : `light placed by ${lightSrc} — the anchor still drags`),
        r.x + 6, r.y + r.h - 5);
    }

    // The path is only live in path mode: drawing a route that nothing
    // follows is the confusing half of a mode-less tool.
    this.pathMode = posMode === "path";
    const tool = pointTool(this.node);
    if (tool && tool.widget === "track_points") {
      // A tracker is a feature region inside a search region, drawn at the
      // sizes actually being used so the boxes mean something: you can see
      // whether the patch holds anything distinctive and whether the search
      // is wide enough for the motion.
      const marks = parsePath(getStr(this.node, "track_points")).slice(0, 2);
      const fpx = getVal(this.node, "track_feature", 32);
      const spx = getVal(this.node, "track_search", 48);
      // feature/search are in FRAME pixels; the backdrop tells us how many
      // of those fit across the picker so the boxes are drawn true to size
      const scale = r.w / Math.max(this.backdrop?.width || r.w, 1);
      marks.forEach((p, i) => {
        const sx = r.x + p[0] * r.w, sy = r.y + p[1] * r.h;
        const f = Math.max(6, fpx * scale) / 2;
        const q = Math.max(10, (fpx / 2 + spx) * scale);
        ctx.strokeStyle = i === 0 ? "#ffb454" : "#66d9ff";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(sx - f, sy - f, f * 2, f * 2);
        ctx.setLineDash([3, 3]);
        ctx.globalAlpha = 0.6;
        ctx.strokeRect(sx - q, sy - q, q * 2, q * 2);
        ctx.globalAlpha = 1;
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(sx - f - 4, sy); ctx.lineTo(sx + f + 4, sy);
        ctx.moveTo(sx, sy - f - 4); ctx.lineTo(sx, sy + f + 4); ctx.stroke();
        ctx.fillStyle = ctx.strokeStyle;
        ctx.font = "10px sans-serif";
        ctx.fillText(i === 0 ? "light" : "anchor", sx + f + 6, sy - f - 2);
      });
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(r.x, r.y + 6, r.w, 15);
      ctx.fillStyle = "#ffe9cf";
      ctx.font = "10px sans-serif";
      ctx.fillText(marks.length === 0
        ? "click a feature with contrast to place the light tracker"
        : marks.length === 1
          ? "click again to add an anchor tracker (rotation + scale), or leave it at one"
          : "drag a tracker to move it · shift-click to remove", r.x + 6, r.y + 17);
    }
    const pts = this.pathMode ? parsePath(getStr(this.node, "light_path")) : [];
    if (pts.length) {
      const P = (p) => [r.x + p[0] * r.w, r.y + p[1] * r.h];
      const curve = samplePath(pts, Math.max(pts.length * 24, 48));
      ctx.strokeStyle = this.pathMode ? "#7ce38b" : "rgba(124,227,139,0.5)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      curve.forEach((p, i) => {
        const [sx, sy] = P(p);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.stroke();
      // travel direction: the light runs start -> end across the batch
      const [ex, ey] = P(curve[curve.length - 1]);
      const [bx, by] = P(curve[Math.max(curve.length - 6, 0)]);
      const ang = Math.atan2(ey - by, ex - bx);
      ctx.fillStyle = "#7ce38b";
      ctx.beginPath();
      ctx.moveTo(ex, ey);
      ctx.lineTo(ex - 9 * Math.cos(ang - 0.4), ey - 9 * Math.sin(ang - 0.4));
      ctx.lineTo(ex - 9 * Math.cos(ang + 0.4), ey - 9 * Math.sin(ang + 0.4));
      ctx.closePath(); ctx.fill();
      if (this.pathMode) {
        pts.forEach((p, i) => {
          const [sx, sy] = P(p);
          ctx.fillStyle = i === 0 ? "#7ce38b" : "#0d0d11";
          ctx.strokeStyle = "#7ce38b";
          ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2);
          ctx.fill(); ctx.stroke();
        });
      }
    }
    // the baked anchor path, read-only: edit the light's points, the anchor
    // keeps the pair's geometry frame for frame
    const aps = this.pathMode ? parsePath(getStr(this.node, "anchor_path")) : [];
    if (aps.length > 1) {
      const P = (p) => [r.x + p[0] * r.w, r.y + p[1] * r.h];
      const curve = samplePath(aps, Math.max(aps.length * 8, 48));
      ctx.strokeStyle = "rgba(95,215,255,0.7)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      curve.forEach((p, i) => {
        const [sx, sy] = P(p);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.stroke();
    }
    this._pathChip = null;
    if (this.pathMode) {
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(r.x, r.y + 6, r.w, 15);
      ctx.fillStyle = "#dfffe6";
      ctx.font = "10px sans-serif";
      ctx.fillText(pts.length
        ? "drag a point to move it · shift-click to remove"
        : "click to start drawing the light's path", r.x + 6, r.y + 17);
    }

    // light handle: a plain ring and dot — no sun-ray decoration, which
    // read as a rendered sun on the backdrop
    const scene=sceneDocument(this.node),selected=activeGroup(scene);
    for(const group of Array.isArray(scene?.groups) ? scene.groups : []){
      if(group.id===selected?.id || group.enabled===false)continue;
      const tracked=this.node._fcGroupResults?.[group.id]?.track?.[0];
      const u=tracked?.[0] ?? group.source?.light_x ?? .25;
      const v=tracked?.[1] ?? group.source?.light_y ?? .3;
      const sx=r.x+u*r.w,sy=r.y+v*r.h;
      ctx.strokeStyle='#999';ctx.fillStyle='#bbb';ctx.lineWidth=1;ctx.font='10px sans-serif';
      ctx.beginPath();ctx.arc(sx,sy,6,0,Math.PI*2);ctx.stroke();ctx.fillText(group.name || 'Flare',sx+9,sy-7);
    }
    ctx.strokeStyle = driven ? "#8a8a92" : "#ffb648";
    ctx.fillStyle = driven ? "rgba(138,138,146,0.16)" : "rgba(255,182,72,0.18)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(lx, ly, 9, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = driven ? "#8a8a92" : "#ffb648";
    ctx.beginPath(); ctx.arc(lx, ly, 2.5, 0, Math.PI * 2); ctx.fill();

    ctx.strokeStyle = anchorDriven ? "#8a8a92" : "#5fd7ff";
    ctx.fillStyle = anchorDriven ? "rgba(138,138,146,0.16)" : "rgba(95,215,255,0.18)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(fx, fy, 9, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(fx - 13, fy); ctx.lineTo(fx + 13, fy);
    ctx.moveTo(fx, fy - 13); ctx.lineTo(fx, fy + 13);
    ctx.stroke();

    ctx.font = "10px sans-serif";
    ctx.fillStyle = "#ffb648";
    ctx.fillText("light", lx + 13, ly - 10);
    ctx.fillStyle = "#5fd7ff";
    ctx.fillText("flare anchor", fx + 13, fy + 18);
  }

  eventPos(e) {
    const rect = this.canvas.getBoundingClientRect();
    const [W, H] = this.cssSize;
    return [((e.clientX - rect.left) / rect.width) * W,
            ((e.clientY - rect.top) / rect.height) * H];
  }

  onPointer(e) {
    const [x, y] = this.eventPos(e);
    const r = this.imageRect();

    const toUV = () => [Math.min(UV_MAX, Math.max(UV_MIN, (x - r.x) / r.w)),
                        Math.min(UV_MAX, Math.max(UV_MIN, (y - r.y) / r.h))];
    // read the mode rather than the flag draw() caches, so a click that
    // lands before the first repaint still does the right thing
    const tool = pointTool(this.node);
    const pathMode = !!tool;
    const TARGET = tool ? tool.widget : "light_path";

    if (e.type === "pointerdown") {
      if (pathMode) {
        // the anchor is still a real control in path mode, so it wins a
        // click near it; everywhere else edits the path
        const [ax, ay] = this.point("flare_x", "flare_y", r);
        const anchorOnPath = TARGET === "light_path"
          && parsePath(getStr(this.node, "anchor_path")).length > 1;
        if (Math.hypot(x - ax, y - ay) <= 11 && !anchorOnPath) {
          this.drag = "flare";
          this.canvas.setPointerCapture(e.pointerId);
          e.stopPropagation(); e.preventDefault();
          return;
        }
        const pts = parsePath(getStr(this.node, TARGET));
        const hit = pts.findIndex((p) =>
          Math.hypot(x - (r.x + p[0] * r.w), y - (r.y + p[1] * r.h)) <= 8);
        if (e.shiftKey) {                       // shift-click removes a point
          if (hit >= 0) {
            pts.splice(hit, 1);
            setStr(this.node, TARGET, formatPath(pts));
          }
        } else if (hit >= 0) {
          this.drag = "point";
          this.dragPoint = hit;
          this.canvas.setPointerCapture(e.pointerId);
        } else {
          if (pts.length >= tool.max) pts.pop();   // a tracker pair is full
          pts.push(toUV());
          setStr(this.node, TARGET, formatPath(pts));
          this.drag = "point";
          this.dragPoint = pts.length - 1;
          this.canvas.setPointerCapture(e.pointerId);
        }
        this.node.setDirtyCanvas(true, false);
        this.draw();
        e.stopPropagation(); e.preventDefault();
        return;
      }
      const [lx, ly] = this.point("light_x", "light_y", r);
      const [fx, fy] = this.point("flare_x", "flare_y", r);
      const dl = Math.hypot(x - lx, y - ly);
      const df = Math.hypot(x - fx, y - fy);
      this.drag = dl <= df ? "light" : "flare";
      this.canvas.setPointerCapture(e.pointerId);
    } else if (e.type === "pointermove" && this.drag === "point") {
      const pts = parsePath(getStr(this.node, TARGET));
      if (this.dragPoint >= 0 && this.dragPoint < pts.length) {
        pts[this.dragPoint] = toUV();
        setStr(this.node, TARGET, formatPath(pts));
        this.node.setDirtyCanvas(true, false);
        this.draw();
      }
    } else if (e.type === "pointermove" && this.drag) {
      const u = Math.min(UV_MAX, Math.max(UV_MIN, (x - r.x) / r.w));
      const v = Math.min(UV_MAX, Math.max(UV_MIN, (y - r.y) / r.h));
      setVal(this.node, this.drag === "light" ? "light_x" : "flare_x", u);
      setVal(this.node, this.drag === "light" ? "light_y" : "flare_y", v);
      this.node.setDirtyCanvas(true, false);
      this.draw();
    } else if (e.type === "pointerup" || e.type === "pointercancel") {
      if (!this.drag) return;
      this.drag = null;
      this.dragPoint = -1;
      try { this.canvas.releasePointerCapture(e.pointerId); } catch {}
    } else {
      return;
    }
    e.stopPropagation();
    e.preventDefault();
  }

  setBackdrop(img) {
    this.backdrop = img;
    this.draw();
  }
}

/* ---------------------------------------------------------- stack editor */

// Menu entries are looks, not engine types: several map to the same
// procedural type with tuned parameters, matching the classic element set
// (glow, disc, iris, multi-iris, spike ball, shimmer, sparkle, rays,
// streak, stripe, ring, hoop, spectral, fog) plus library textures.
// Which library category holds alternatives for each element look — the
// name-click gallery filters to this, so a glow offers other glows.
// The library's eight canonical families - every look maps into one, and
// the forge's prompt categories use the same eight names, so what you make
// in the forge lands exactly where the gallery looks for it.
const CATEGORY_OF = {
  glow: "glows", bloom: "glows", fog: "glows",
  disc: "ghosts", iris: "ghosts", "multi-iris": "ghosts",
  "spike ball": "rays", shimmer: "rays", sparkle: "rays",
  rays: "rays", glint: "rays",
  streak: "streaks", stripe: "streaks",
  ring: "rings", spectral: "rings", hoop: "hoops",
  caustic: "caustics", "lens dirt": "lens_dirt",
  "lens orbs": "ghosts", orbs: "ghosts",
  crescent: "rings", "edge hotspot": "glows", "ghost streak": "streaks",
};

// Old presets and saved workflows may carry pre-consolidation family names.
const LEGACY_CATEGORY = {
  fog: "glows", discs: "ghosts", iris_ghosts: "ghosts", lens_orbs: "ghosts",
  dirt_bokeh: "ghosts", spike_balls: "rays", shimmers: "rays",
  sparkles: "rays", stripes: "streaks", spectral: "rings",
};

const ADD_MENU = [
  ["Glow", "glow"], ["Bloom (bright areas)", "bloom"],
  ["Lens dirt (bright areas)", "lens_dirt"],
  ["Lens orbs (procedural)", "orbs"],
  ["Fog", "fog"], ["Disc", "disc"],
  ["Iris ghost", "iris"], ["Multi-iris", "multi_iris"],
  ["Spike ball", "spike_ball"], ["Shimmer", "shimmer"],
  ["Sparkle", "sparkle"], ["Rays", "rays"],
  ["Streak", "streak"], ["Stripe", "stripe"],
  ["Ring", "ring"], ["Hoop", "hoop"], ["Spectral", "spectral"],
  ["Crescent (clipped ghost)", "crescent"],
  ["Edge hotspot (frame edge)", "edge_hotspot"],
  ["Ghost streak (below source)", "ghost_streak"],
  ["From library…", "texture"],
];

const ADD_DEFAULTS = {
  glow: { type: "glow", label: "glow", offset: 0, scale: 0.4, intensity: 1, color: [1, 0.95, 0.85], params: { softness: 0.35, falloff: 1.3 } },
  bloom: { type: "glow", label: "bloom", offset: 0, scale: 2.2, intensity: 0.5, auto_rotate: false, light_mask: 1, color: [1, 0.97, 0.9], params: { softness: 1.1, falloff: 0.7 } },
  lens_dirt: { type: "texture", label: "lens dirt", offset: 0, scale: 1.0, intensity: 0.7, auto_rotate: false, screen_space: true, fill_frame: true, light_mask: 1, mask_floor: 0.35, mask_scene: 0, params: { file: "", channel: "auto" } },
  fog: { type: "glow", label: "fog", offset: 0, scale: 1.6, intensity: 0.25, color: [1, 0.97, 0.9], params: { softness: 0.8, falloff: 0.8 } },
  disc: { type: "iris", label: "disc", offset: 0.5, scale: 0.16, intensity: 0.3, color: [0.8, 0.9, 1], params: { blades: 24, edge_softness: 0.55 } },
  iris: { type: "iris", label: "iris", offset: 0.7, scale: 0.12, intensity: 0.25, color: [0.85, 0.93, 1], dispersion: 0.4, params: { blades: 8, edge_softness: 0.3 } },
  multi_iris: { type: "iris", label: "multi-iris", offset: 0.25, scale: 0.07, intensity: 0.15, count: 8, spread: 0.22, count_falloff: 0.82, count_scale_step: 1.25, color: [0.85, 0.93, 1], dispersion: 0.35, params: { blades: 7, edge_softness: 0.35 } },
  spike_ball: { type: "glint", label: "spike ball", offset: 0, scale: 0.55, intensity: 0.9, color: [1, 0.97, 0.9], params: { points: 48, length: 0.55, thickness: 0.004, length_jitter: 0.5 } },
  shimmer: { type: "glint", label: "shimmer", offset: 0, scale: 0.85, intensity: 0.7, color: [0.95, 0.95, 1], params: { points: 60, length: 0.8, thickness: 0.003, length_jitter: 0.6 } },
  sparkle: { type: "glint", label: "sparkle", offset: 0, scale: 0.5, intensity: 0.5, color: [1, 1, 1], params: { points: 90, length: 0.45, thickness: 0.002, length_jitter: 0.8 } },
  rays: { type: "glint", label: "rays", offset: 0, scale: 1.0, intensity: 0.8, color: [1, 0.96, 0.88], params: { points: 10, length: 1.1, thickness: 0.012, length_jitter: 0.55 } },
  streak: { type: "streak", label: "streak", offset: 0, scale: 1, intensity: 0.6, auto_rotate: false, color: [0.5, 0.7, 1], params: { length: 1.4, thickness: 0.012 } },
  stripe: { type: "streak", label: "stripe", offset: 0, scale: 1.2, intensity: 0.5, auto_rotate: false, rotation: 12, color: [0.8, 0.85, 1], params: { length: 2.0, thickness: 0.004 } },
  ring: { type: "ring", label: "ring", offset: 1.4, scale: 0.4, intensity: 0.15, dispersion: 0.8, color: [1, 0.95, 1], params: { radius: 1, thickness: 0.08 } },
  hoop: { type: "hoop", label: "hoop", offset: 0.5, scale: 0.8, intensity: 0.12, dispersion: 1, color: [1, 0.8, 0.6], params: { radius: 0.9, thickness: 0.22, angular_falloff: 0.8 } },
  // dispersion is stated rather than left implicit: the schema gives this
  // type 1.0 / 7 samples of its own, and a row that showed 0 while the
  // engine rendered a rainbow was simply lying about itself
  spectral: { type: "spectral", label: "spectral", offset: 1.7, scale: 0.45, intensity: 0.15, dispersion: 1, dispersion_samples: 7, params: { shape: "ring", radius: 1, thickness: 0.06 } },
  crescent: { type: "ring", label: "crescent", offset: 1.2, scale: 0.35, intensity: 0.3, dispersion: 0.6, shade: 0.4, color: [1, 0.88, 0.62], params: { radius: 0.92, thickness: 0.13, crescent: 0.55, crescent_feather: 0.2 } },
  edge_hotspot: { type: "glow", label: "edge hotspot", offset: 0, scale: 0.035, intensity: 0.5, auto_rotate: false, stretch: [0.35, 1.6], pin: [null, -1], color: [0.55, 0.9, 1], blur: 0.08, params: { softness: 0.35, falloff: 2 } },
  ghost_streak: { type: "streak", label: "ghost streak", offset: 0, scale: 1, intensity: 0.2, auto_rotate: false, shift: [0, 0.6], color: [0.15, 0.35, 1], blur: 0.12, params: { length: 2.2, thickness: 0.01, curve: 0.3 } },
  texture: { type: "texture", label: "element", offset: 0.6, scale: 0.3, intensity: 0.6, params: { file: "", channel: "auto" } },
  orbs: { type: "orbs", label: "lens orbs", offset: 0, scale: 1.0, intensity: 0.35, auto_rotate: false, screen_space: true, color: [0.9, 0.95, 1], params: { count: 24, size: 0.12, size_jitter: 0.6, spread: 1.2, edge_softness: 0.3, illumination: 0.8, shape: "disc", blades: 6 } },
};

// Trigger block defaults (mirror of the schema's TRIGGER_DEFAULTS).
const TRIGGER_DEFAULTS = {
  mode: "none", source: "light", inner: 0, outer: 0.3, falloff: "smooth",
  brightness: 0, scale: 0, rotation: 0, color: [1, 1, 1],
};
const TRIGGER_SPECS = {
  inner: [-1, 2, 0.01], outer: [-1, 2, 0.01],
  brightness: [-2, 8, 0.05], scale: [-0.9, 6, 0.05],
  rotation: [-180, 180, 1],
};

// Port of flare/engine.py trigger_factor() — how strongly a rule fires at a
// point in grid coordinates, used to paint the trigger preview. The Python
// test test_trigger_factor_reference_values pins the values both must
// produce; if you change one, change the other.
function triggerRamp(t, falloff) {
  t = Math.min(Math.max(t, 0), 1);
  if (falloff === "linear") return t;
  if (falloff === "exponential") return t * t;
  return t * t * (3 - 2 * t);
}

function triggerFactor(trig, x, y, frameAspect, lx = 0, ly = 0) {
  if (!trig || trig.mode === "none") return 0;
  let d;
  if (trig.mode === "border") d = Math.min(frameAspect - Math.abs(x), 1 - Math.abs(y));
  else if (trig.mode === "light") d = Math.hypot(x - lx, y - ly);
  else d = Math.hypot(x, y);
  const span = Math.max(trig.outer - trig.inner, 1e-6);
  return 1 - triggerRamp((d - trig.inner) / span, trig.falloff);
}

// A lens-surface texture (dirt, droplets) is not a shape on black: it covers
// the front element and is revealed only where the light falls. Choosing one
// from the library therefore carries the plate treatment with it, the same
// way FlareTexturePrepare's frame: auto reads the category — without it the
// element renders as a full-frame wash and the reveal has to be discovered
// knob by knob. Only applied while the mask settings are untouched, so a
// deliberate look is never overwritten.
const LENS_PLATE_SLOTS = new Set(["lens_dirt"]);
function applyLensPlateDefaults(elem, ref) {
  if (!LENS_PLATE_SLOTS.has(String(ref).split("/")[0])) return;
  if (elem.light_mask || elem.fill_frame) return;   // the owner has decided
  elem.fill_frame = true;
  elem.screen_space = true;
  elem.light_mask = 1;
  elem.mask_floor = 0.35;
  elem.mask_scene = 0;   // lit by the source, not by the moving frame
  elem.auto_rotate = false;
}

// Element settings copied with "copy settings" (everything but identity).
let clipboardElem = null;

const COMMON_SPECS = {
  irregular: [0, 1, 0.01],
  light_mask: [0, 1, 0.01],
  mask_scene: [0, 1, 0.01],
  mask_floor: [0, 0.95, 0.01],
  shade: [-1, 1, 0.01],
  dispersion: [0, 3, 0.05], dispersion_samples: [3, 15, 2],
  rotation: [-180, 180, 1], count: [1, 24, 1], spread: [0, 1, 0.01],
  count_falloff: [0.1, 1, 0.01], count_scale_step: [0.5, 2, 0.01],
};

// What an ABSENT key means (the schema's defaults) — without these an unset
// slider would display its range minimum, e.g. rotation reading -180.
const COMMON_DEFAULTS = {
  irregular: 0,
  light_mask: 0,
  mask_scene: 1,
  mask_floor: 0,
  shade: 0,
  dispersion: 0, dispersion_samples: 3, rotation: 0, count: 1, spread: 0,
  count_falloff: 1, count_scale_step: 1,
};

const COMPLETION_FALLBACK = { completion: 360, completion_feather: 0.2 };
const COMPLETION_SPEC = { completion: [10, 360, 1], completion_feather: [0, 1, 0.01] };

const CRESCENT_FALLBACK = { crescent: 0, crescent_feather: 0.1 };
const CRESCENT_SPEC = { crescent: [0, 0.98, 0.01], crescent_feather: [0.001, 1, 0.005] };

const PARAM_FALLBACKS = {
  glow: { softness: 0.35, falloff: 1.2 },
  iris: { blades: 6, edge_softness: 0.15, hollow: 0, roundness: 0, ...CRESCENT_FALLBACK },
  streak: { length: 0.8, thickness: 0.02, count: 1, curve: 0, dash: 0 },
  ring: { radius: 0.5, thickness: 0.05, ...COMPLETION_FALLBACK, ...CRESCENT_FALLBACK },
  hoop: { radius: 0.6, thickness: 0.15, angular_falloff: 0.8, ...COMPLETION_FALLBACK, ...CRESCENT_FALLBACK },
  glint: { points: 8, length: 0.5, thickness: 0.008, length_jitter: 0.3, ...COMPLETION_FALLBACK },
  spectral: { radius: 0.5, thickness: 0.08, blades: 8, edge_softness: 0.1, hollow: 0, roundness: 0, ...COMPLETION_FALLBACK, ...CRESCENT_FALLBACK },
  texture: {},
  orbs: { count: 24, size: 0.12, size_jitter: 0.6, spread: 1.0, edge_softness: 0.3, illumination: 0.8, blades: 6, ring: 0, ring_width: 0.3, spectral: 0 },
};

const PARAM_SPECS = {
  glow: { softness: [0.01, 2, 0.01], falloff: [0.05, 6, 0.05] },
  iris: { blades: [3, 24, 1], edge_softness: [0, 1, 0.01], hollow: [0, 0.95, 0.01], roundness: [0, 1, 0.01], ...CRESCENT_SPEC },
  streak: { length: [0.01, 4, 0.01], thickness: [0.001, 0.5, 0.001], count: [1, 8, 1], curve: [-1, 1, 0.01], dash: [0, 1, 0.01] },
  ring: { radius: [0, 2, 0.01], thickness: [0.001, 0.5, 0.001], ...COMPLETION_SPEC, ...CRESCENT_SPEC },
  hoop: { radius: [0, 2, 0.01], thickness: [0.001, 1, 0.001], angular_falloff: [0, 1, 0.01], ...COMPLETION_SPEC, ...CRESCENT_SPEC },
  glint: { points: [1, 256, 1], length: [0.01, 3, 0.01], thickness: [0.001, 0.1, 0.001], length_jitter: [0, 1, 0.01], ...COMPLETION_SPEC },
  spectral: { radius: [0, 2, 0.01], thickness: [0.001, 0.5, 0.001], blades: [3, 24, 1], edge_softness: [0, 1, 0.01], hollow: [0, 0.95, 0.01], roundness: [0, 1, 0.01], ...COMPLETION_SPEC, ...CRESCENT_SPEC },
  texture: {},
  orbs: { count: [1, 200, 1], size: [0.01, 1, 0.005], size_jitter: [0, 1, 0.01], spread: [0, 3, 0.01], edge_softness: [0, 1, 0.01], illumination: [0.05, 5, 0.01], blades: [3, 16, 1], ring: [0, 1, 0.01], ring_width: [0.01, 2, 0.01], spectral: [0, 1, 0.01] },
};

const CSS = `
.fcore { font: 12px/1.4 sans-serif; color: #ccc; background: #131317;
  border: 1px solid #2b2b33; border-radius: 4px; padding: 8px;
  display: flex; flex-direction: column; gap: 7px; box-sizing: border-box;
  height: 100%; overflow-x:hidden; overflow-y:auto; }
.fcore * { box-sizing: border-box; }
.fcore > :not(.fcore-list) { flex-shrink:0; }
.fcore > .fcore-list { min-height:120px; }
.fcore-bar { display: flex; gap: 8px; align-items: center; flex-wrap:wrap; width:100%; }
.fcore-bar > .fcore-btn { min-height:36px; padding:7px 12px; }
.fcore-bar .fcore-preset-button { flex:1 1 200px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; text-align:left; }
.fcore-preset-gallery { box-sizing:border-box; width:min(1050px,94vw); max-height:88vh; padding:18px; border:1px solid #3a3a44; border-radius:10px; background:#19191f; color:#ddd; font:13px sans-serif; }
.fcore-preset-gallery::backdrop { background:#0009; }
.fcore-preset-gallery header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.fcore-preset-gallery h2 { margin:0 0 12px; font-size:20px; }
.fcore-preset-gallery input[type=search] { box-sizing:border-box; width:100%; height:36px; margin:8px 0 14px; padding:8px; border:1px solid #3a3a44; border-radius:4px; background:#101014; color:#eee; }
.fcore-preset-body { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); gap:18px; }
.fcore-preset-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(145px,1fr)); grid-auto-rows:max-content; align-content:start; gap:9px; overflow:auto; max-height:55vh; padding:2px; }
.fcore-preset-card { min-width:0; padding:0 0 9px; border:1px solid #34343e; border-radius:6px; overflow:hidden; background:#1e1e25; color:#ddd; cursor:pointer; text-align:left; }
.fcore-preset-card:hover,.fcore-preset-card:focus-visible,.fcore-preset-card.selected { border-color:#e8a33d; outline:1px solid #e8a33d; }
.fcore-preset-card.preview-selected { border-color:#e8a33d; outline:2px solid #e8a33d; background:#30271c; }
.fcore-preset-card img { display:block; width:100%; aspect-ratio:16/9; object-fit:contain; background:#050508; }
.fcore-preset-card span,.fcore-preset-card small { display:block; padding:7px 9px 0; overflow-wrap:anywhere; }
.fcore-preset-card small { color:#aaa; }
.fcore-preset-gallery .fcore-preset-card { display:flex!important; flex-direction:column!important; height:auto!important; min-height:145px!important; max-height:none!important; align-self:start; justify-content:flex-start; line-height:1.35; white-space:normal!important; }
.fcore-preset-gallery .fcore-preset-card img { width:100%!important; height:auto!important; max-height:none!important; flex:0 0 auto; aspect-ratio:16/9; object-fit:contain!important; }
.fcore-preset-gallery .fcore-preset-card span,.fcore-preset-gallery .fcore-preset-card small { display:block!important; flex:0 0 auto; height:auto!important; min-height:1.3em; white-space:normal; text-align:left; }
.fcore-preset-category { display:block; width:100%; height:34px!important; margin:0 0 12px; border:1px solid #3a3a44; border-radius:4px; background:#202027; color:#eee; }
.fcore-groups { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:8px 0; border-bottom:1px solid #34343e; margin-bottom:8px; }
.fcore-groups .fcore-btn { min-height:30px; }
.fcore-groups .active { border-color:#e8a33d; color:#ffd08a; background:#30271e; }
.fcore-group-title { flex-basis:100%; color:#aaa; font-size:11px; }
.fcore-preset-preview { min-width:0; }
.fcore-preset-preview img { display:block; width:100%; aspect-ratio:16/9; object-fit:contain; background:#050508; border-radius:6px; }
.fcore-preset-preview p { color:#aaa; line-height:1.5; }
.fcore-preset-actions { display:flex; gap:8px; flex-wrap:wrap; }
@media(max-width:620px) { .fcore-preset-body { grid-template-columns:1fr; } .fcore-preset-grid { max-height:28vh; } .fcore-preset-preview img { max-height:22vh; } }
.fcore-btn { background: #1e1e25; color: #ddd; border: 1px solid #34343e;
  border-radius: 3px; padding: 5px 12px; cursor: pointer; font-size: 12px; }
.fcore-btn:hover { background: #2a2a33; border-color: #e8a33d; }
.fcore-btn.accent { color: #e8a33d; }
.fcore-btn.on { border-color: #e8a33d; color: #e8a33d; }
.fcore-badge { color: #ff7676; margin-left: auto; font-size: 11px; }
.fcore-list { overflow-y: auto; display: flex; flex-direction: column;
  gap: 5px; flex: 1; min-height: 60px; }
.fcore-row { background: #1a1a20; border: 1px solid #2b2b33;
  border-radius: 4px; padding: 7px 9px; }
.fcore-row.off { opacity: 0.4; }
.fcore-head { display: flex; align-items: center; gap: 8px; }
.fcore-chip { width: 44px; height: 44px; border-radius: 3px; flex: 0 0 44px;
  background: #101014; border: 1px solid #2b2b33; overflow: hidden;
  display: flex; align-items: center; justify-content: center; }
.fcore-chip img { width: 100%; height: 100%; object-fit: cover; }
.fcore-chip .ico { width: 24px; height: 24px; display: block; }
.ico-glow { border-radius: 50%; background: radial-gradient(circle,#ffe9b8 0%,#ffb648 45%,transparent 75%); }
.ico-iris { background: #9db8d8aa; clip-path: polygon(50% 0,90% 25%,90% 75%,50% 100%,10% 75%,10% 25%); }
.ico-streak { height: 3px !important; align-self: center; border-radius: 2px;
  background: linear-gradient(90deg,transparent,#7fd8d8,#d8f6f6,#7fd8d8,transparent); }
.ico-ring { border-radius: 50%; border: 2.5px solid #e07fd8; background: transparent; }
.ico-hoop { border-radius: 50%; border: 2.5px solid transparent;
  border-top-color: #ffb648; border-right-color: #e07f7f; transform: rotate(-40deg); }
.ico-glint { background:
  conic-gradient(from 0deg,#fff 0 4deg,transparent 4deg 26deg,#ddd 26deg 30deg,
  transparent 30deg 56deg,#fff 56deg 60deg,transparent 60deg 86deg,#ddd 86deg 90deg,
  transparent 90deg 116deg,#fff 116deg 120deg,transparent 120deg 146deg,#ddd 146deg 150deg,
  transparent 150deg 176deg,#fff 176deg 180deg,transparent 180deg 206deg,#ddd 206deg 210deg,
  transparent 210deg 236deg,#fff 236deg 240deg,transparent 240deg 266deg,#ddd 266deg 270deg,
  transparent 270deg 296deg,#fff 296deg 300deg,transparent 300deg 326deg,#ddd 326deg 330deg,
  transparent 330deg 356deg,#fff 356deg 360deg); border-radius: 50%; opacity: .8; }
.ico-spectral { height: 8px !important; align-self: center; border-radius: 4px;
  background: linear-gradient(90deg,#f44,#fa4,#ff4,#4f4,#4ff,#44f,#a4f); }
.fcore-name { min-width: 68px; max-width: 96px; color: #eee; font-weight: 600;
  cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fcore-name:hover { color: #e8a33d; }
.fcore-col { display: flex; flex-direction: column; gap: 2px; flex: 1;
  min-width: 62px; }
.fcore-col label { color: #7a7a86; font-size: 10px; text-align: center; }
.fcore-info { display: inline-block; width: 11px; height: 11px; line-height: 10px;
  margin-left: 4px; border-radius: 50%; border: 1px solid #5c5c68; color: #8d8d9b;
  font: italic 8px/10px serif; text-align: center; cursor: help; vertical-align: middle;
  opacity: 0.75; user-select: none; }
.fcore-info:hover { opacity: 1; border-color: #e8a33d; color: #e8a33d; }
.fcore-tip { position: fixed; z-index: 10000; max-width: 250px; background: #1c1c22;
  color: #e6e6ee; border: 1px solid #3a3a46; border-radius: 3px; padding: 6px 9px;
  font-size: 11px; line-height: 1.4; box-shadow: 0 4px 16px rgba(0,0,0,0.55);
  pointer-events: none; text-align: left; }
.fcore-col input[type=range] { width: 100%; height: 12px; accent-color: #e8a33d; }
.fcore-col input[type=number] { width: 100%; background: #101014; color: #ddd;
  border: 1px solid #2b2b33; border-radius: 3px; font-size: 11px;
  padding: 2px 4px; text-align: center; -moz-appearance: textfield; }
.fcore-col input[type=number]::-webkit-inner-spin-button { display: none; }
.fcore-swatch { width: 24px; height: 24px; padding: 0; border: 1px solid #34343e;
  border-radius: 3px; background: none; cursor: pointer; flex: 0 0 24px; }
.fcore-acts { display: flex; gap: 3px; }
.fcore-mini { background: #1e1e25; border: 1px solid #2b2b33; color: #999;
  cursor: pointer; border-radius: 3px; width: 26px; height: 26px;
  font-size: 12px; display: flex; align-items: center; justify-content: center; }
.fcore-mini:hover { color: #fff; border-color: #e8a33d; }
.fcore-mini.on { background: #2f4a75; border-color: #4f7ac0; color: #fff; }
.fcore-mini:disabled { opacity: .35; cursor: default; }
.fcore-row.dim { opacity: .4; }
.fcore-row.trig { border-left: 3px solid #e84848; padding-left: 5px; }
.fcore-mini.armed { background: #5b2626; border-color: #e84848; color: #ffd7d7; }
.fcore-sec { grid-column: 1 / -1; color: #8a8fa8; font-size: 11px;
  letter-spacing: .04em; text-transform: uppercase; margin-top: 4px;
  border-top: 1px solid #2b2b33; padding-top: 5px; }
.fcore-global.lens { margin-top: 4px; }
.fcore-global.src { flex-wrap: wrap; }
.fcore-source-tuning { flex-basis:100%; min-width:0; border-top:1px solid #34343e; padding-top:8px; }
.fcore-source-tuning > summary { color:#aaa; cursor:pointer; }
.fcore-source-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(150px,100%),1fr)); gap:10px; padding-top:10px; }
.fcore-src-sel { background: #1e1e25; color: #ddd; border: 1px solid #34343e;
  border-radius: 3px; font-size: 12px; height: 24px; padding: 0 6px; }
.fcore-hint { color: #7f8496; font-size: 11px; flex-basis: 100%;
  line-height: 1.35; }
.fcore-forge { display: flex; flex-direction: column; gap: 8px; height: 100%;
  box-sizing: border-box; }
.fcore-forge textarea { background: #101014; color: #ddd; flex: 1 1 auto;
  border: 1px solid #34343e; border-radius: 4px; font: 12px/1.45 sans-serif;
  padding: 8px; resize: vertical; min-height: 150px; }
.fcore-forge textarea:focus { outline: none; border-color: #e8a33d; }
.fcore-forge .rowline { display: flex; gap: 8px; align-items: center; }
.fcore-forge .styletext { background: #101014; color: #ddd;
  border: 1px solid #34343e; border-radius: 4px; font: 11px/1.4 sans-serif;
  padding: 6px 8px; resize: vertical; min-height: 52px; flex: 0 0 auto;
  width: 100%; box-sizing: border-box; }
.fcore-forge .styletext:focus { outline: none; border-color: #e8a33d; }
/* the hint carries flex-basis:100% so it wraps onto its own line inside the
   horizontal source row; in this COLUMN panel that reads as 100% of the
   HEIGHT and it swallows every spare pixel meant for the prompt box. */
.fcore-forge .fcore-hint { flex: 0 0 auto; }
.fcore-forge { padding:14px; gap:12px; overflow-y:auto; }
.fcore-forge h3 { margin:0; font-size:16px; color:#eee; }
.fcore-forge .forge-intro { margin:4px 0 0; color:#9696a5; font-size:12px; }
.forge-card { border:1px solid #34343e; border-radius:6px; padding:12px; min-width:0; flex-shrink:0; background:#1a1a20; }
.forge-card > summary { cursor:pointer; color:#ddd; }
.forge-card .rowline { flex-wrap:wrap; align-items:center; gap:10px; }
.forge-card label { display:flex; flex-direction:column; gap:6px; min-width:0; flex:1 1 140px; color:#aaa; }
.forge-card .fcore-src-sel { width:100%; height:34px; min-width:0; }
.forge-card-title { color:#e8a33d; font-size:11px; letter-spacing:.05em; margin-bottom:10px; }
.forge-prompt { flex:1 0 240px; display:flex; flex-direction:column; gap:8px; }
.forge-prompt textarea { width:100%; min-height:160px; line-height:1.6; padding:10px; }
.forge-footer { display:flex; gap:10px; justify-content:space-between; align-items:center; }
.forge-style-body { display:flex; flex-direction:column; gap:10px; margin-top:12px; }
.forge-card .forge-style-toggle { flex-direction:row; flex:0 0 auto; align-items:center; }
.fcore-forge input[type=checkbox] { accent-color:#e8a33d; }
.fcore-forge button:focus-visible,.fcore-forge select:focus-visible { outline:2px solid #e8a33d; }

.fcore-adv { display: flex; flex-direction: column; gap: 8px; min-width: 0;
  padding: 8px 0 0; border-top: 1px solid #2b2b33; margin-top: 7px; }
.fcore-settings { min-width: 0; border: 1px solid #34343e; border-radius: 5px; }
.fcore-settings > summary { padding: 9px 12px; cursor: pointer; color: #ddd;
  font-size: 12px; user-select: none; }
.fcore-settings > summary:hover { color: #e8a33d; background: #202027; }
.fcore-settings > summary:focus-visible { outline: 2px solid #e8a33d; }
.fcore-settings-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(180px,100%),1fr));
  gap: 12px; padding: 10px 12px 12px; border-top: 1px solid #2b2b33; min-width: 0; }
.fcore-settings-grid > * { min-width: 0; }
.fcore-settings-grid input[type=checkbox] { accent-color: #e8a33d; }
.fcore-adv select { background: #1e1e25; color: #ddd; border: 1px solid #34343e;
  border-radius: 3px; font-size: 11px; width: 100%; height: 22px; }
.fcore-global { display: flex; gap: 10px; align-items: center;
  background: #1a1a20; border: 1px solid #2b2b33; border-radius: 4px;
  padding: 7px 10px; }
.fcore-global label { color: #aaa; font-size: 12px; }
.fcore-global input[type=range] { flex: 1; accent-color: #e8a33d; height: 12px; }
.fcore-global input[type=number] { width: 56px; background: #101014;
  color: #ddd; border: 1px solid #2b2b33; border-radius: 3px;
  padding: 3px 4px; text-align: center; font-size: 11px; }
.fcore-menu { position: fixed; z-index: 10000; background: #1a1a20;
  border: 1px solid #34343e; border-radius: 4px; padding: 4px;
  display: flex; flex-direction: column; min-width: 160px; max-height: 60vh;
  overflow-y: auto; box-shadow: 0 6px 24px rgba(0,0,0,0.55); }
.fcore-menu button { background: none; border: none; color: #ccc;
  text-align: left; padding: 6px 10px; cursor: pointer; font-size: 12px;
  border-radius: 3px; }
.fcore-menu button:hover { background: #2a2a33; color: #fff; }
.fcore-menu-head { color: #8a8a97; font-size: 10px; letter-spacing: 0.08em;
  text-transform: uppercase; padding: 8px 10px 3px; border-top: 1px solid #2b2b33;
  margin-top: 3px; }
.fcore-menu-head:first-child { border-top: none; margin-top: 0; padding-top: 4px; }
.fcore-shade { position: fixed; inset: 0; z-index: 10001;
  background: rgba(0,0,0,0.6); display: flex; align-items: center;
  justify-content: center; }
.fcore-gal { background: #17171c; border: 1px solid #34343e;
  border-radius: 6px; width: min(760px, 92vw); max-height: 82vh;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 12px 48px rgba(0,0,0,0.7); font: 12px/1.4 sans-serif;
  color: #ccc; }
.fcore-gal-head { display: flex; align-items: center; padding: 12px 16px;
  border-bottom: 1px solid #2b2b33; }
.fcore-gal-head b { color: #eee; font-size: 14px; }
.fcore-gal-head button { margin-left: auto; }
.fcore-gal-body { overflow-y: auto; padding: 10px 16px 16px; }
.fcore-gal-cat { color: #e8a33d; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.08em; margin: 12px 0 6px; }
.fcore-gal-grid { display: grid;
  grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; }
.fcore-thumb { background: #000; border: 1px solid #2b2b33; border-radius: 4px;
  overflow: hidden; cursor: pointer; text-align: center; }
.fcore-thumb:hover { border-color: #e8a33d; }
.fcore-thumb.sel { border-color: #5fd7ff; box-shadow: 0 0 0 1px #5fd7ff; }
.fcore-thumb img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
.fcore-thumb span { display: block; padding: 3px 4px; font-size: 10px;
  color: #999; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fcore-gal-empty { color: #777; text-align: center; padding: 30px 10px; }

/* --- tech pass: sharper corners, a rule down the left of every row, and a
   large hover preview of any picked texture ------------------------------- */
.fcore-row { border-left: 2px solid #3a3a48; }
.fcore-row:hover { border-left-color: #e8a33d; }
.fcore-col label { letter-spacing: 0.4px; }
.fcore-global.src .fcore-col { min-width: 96px; }
.fcore-chip { cursor: pointer; border: 1px solid #3a3a48; box-sizing: border-box; }
.fcore-chip:hover { border-color: #e8a33d; }
.fcore-peek { position: fixed; z-index: 10000; width: 260px; background: #0b0b0e;
  border: 1px solid #e8a33d; border-radius: 3px; padding: 3px; pointer-events: none;
  box-shadow: 0 8px 24px rgba(0,0,0,0.65); }
.fcore-peek img { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block;
  background: #000; }
.fcore-peek span { display: block; color: #cfcfd8; font-size: 10px; padding: 3px 2px 1px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`;

let cssInjected = false;
function injectCSS() {
  if (cssInjected) return;
  cssInjected = true;
  const st = document.createElement("style");
  st.textContent = CSS;
  document.head.appendChild(st);
}

function colorToHex(c) {
  const h = (x) => Math.round(Math.min(1, Math.max(0, x)) * 255)
    .toString(16).padStart(2, "0");
  return `#${h(c[0])}${h(c[1])}${h(c[2])}`;
}
function hexToColor(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
}

function popupMenu(evt, entries, onPick) {
  document.querySelectorAll(".fcore-menu").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "fcore-menu";
  for (const [label, value] of entries) {
    // a null value is a section heading, not something to pick
    if (value === null) {
      const h = document.createElement("div");
      h.className = "fcore-menu-head";
      h.textContent = label;
      menu.appendChild(h);
      continue;
    }
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => { menu.remove(); onPick(value); };
    menu.appendChild(b);
  }
  menu.style.left = `${Math.min(evt.clientX, window.innerWidth - 200)}px`;
  menu.style.top = `${Math.min(evt.clientY, window.innerHeight - 300)}px`;
  document.body.appendChild(menu);
  setTimeout(() => {
    const close = (e) => {
      if (!menu.contains(e.target)) {
        menu.remove();
        document.removeEventListener("pointerdown", close);
      }
    };
    document.addEventListener("pointerdown", close);
  }, 0);
}

// Full-screen element gallery: thumbnails of everything in the library,
// grouped by category. Click a thumbnail -> onPick(ref). Pass `category`
// to show a single category only (used when a texture row's name is
// clicked: alternatives for THIS element, not the whole library).
function openGallery(files, { title = "Element library", selected = null,
                              category = null, onShowAll = null }, onPick) {
  if (category) {
    files = files.filter((f) => f.startsWith(category + "/"));
  }
  document.querySelectorAll(".fcore-shade").forEach((m) => m.remove());
  const shade = document.createElement("div");
  shade.className = "fcore-shade";
  const gal = document.createElement("div");
  gal.className = "fcore-gal";

  const head = document.createElement("div");
  head.className = "fcore-gal-head";
  const caption = document.createElement("b");
  caption.textContent = title;
  const close = document.createElement("button");
  close.className = "fcore-btn";
  close.textContent = "close";
  close.onclick = () => shade.remove();
  head.append(caption);
  if (onShowAll) {
    const all = document.createElement("button");
    all.className = "fcore-btn";
    all.style.marginLeft = "auto";
    all.textContent = "all elements";
    all.title = "browse the whole library instead of this family";
    all.onclick = () => { shade.remove(); onShowAll(); };
    head.appendChild(all);
    close.style.marginLeft = "8px";
  }
  head.appendChild(close);

  const body = document.createElement("div");
  body.className = "fcore-gal-body";

  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "fcore-gal-empty";
    empty.textContent = category
      ? `no ${category.replace(/_/g, " ")} elements yet — pick the ` +
        `'${category}' prompts in the element forge workflow to generate some`
      : "the library is empty — generate elements with the element forge " +
        "workflow, they will appear here";
    body.appendChild(empty);
  } else {
    const cats = {};
    for (const f of files) {
      const cat = f.includes("/") ? f.split("/")[0] : "misc";
      (cats[cat] ||= []).push(f);
    }
    for (const cat of Object.keys(cats).sort()) {
      const h = document.createElement("div");
      h.className = "fcore-gal-cat";
      h.textContent = cat.replace(/_/g, " ");
      body.appendChild(h);
      const grid = document.createElement("div");
      grid.className = "fcore-gal-grid";
      for (const ref of cats[cat]) {
        const t = document.createElement("div");
        t.className = "fcore-thumb" + (ref === selected ? " sel" : "");
        const img = document.createElement("img");
        img.loading = "lazy";
        img.src = elementThumbUrl(ref);
        const name = document.createElement("span");
        name.textContent = ref.split("/").pop().replace(/\.png$/, "");
        t.append(img, name);
        t.onclick = () => { shade.remove(); onPick(ref); };
        grid.appendChild(t);
      }
      body.appendChild(grid);
    }
  }

  gal.append(head, body);
  shade.appendChild(gal);
  shade.addEventListener("pointerdown", (e) => {
    if (e.target === shade) shade.remove();
    e.stopPropagation();
  });
  document.body.appendChild(shade);
}

// slider + numeric box column, labelled above — the mockup's control unit.
// The number box shows the TRUE value even when it exceeds the slider range
// (e.g. an HDR intensity of 1.4 on a 0..1 slider).
// What a control does, in one breath, for the ones that are not obvious
// from their name. Size and opacity need no explanation; where along the
// flare axis "pos" puts an element does. Looked up by the control's label,
// so every slider, checkbox and dropdown with a matching name gets an (i).
const TIPS = {
  // element row
  "pos": "where along the flare axis this element sits: 0 is on the light, 1 is on the anchor, negative is behind the light. Ghosts usually spread between 0.2 and 1.5.",
  "blur": "softens this element only.",
  // shapes drawn from the cine-lens survey
  "shade": "lights the element from one side only, in its own local frame: a ghost bright on the edge facing the source reads as a comet, and as a pointed crescent once the frame cuts it. Negative lights the other edge.",
  "shift x": "a nudge across the SCREEN, applied after the flare axis, so it does not swing as the light moves. Half-frame-heights.",
  "shift y": "a nudge DOWN the screen that ignores the flare angle. This is how a companion ghost keeps its own streak a fixed drop below the source.",
  "pin x to frame": "lock this element's horizontal position to the frame; its vertical position keeps following the light.",
  "pin y to frame": "lock this element's vertical position to the frame. With y pinned to -1 it rides the top edge directly above the light, which is the hotspot most anamorphics throw.",
  "pin x": "where on the frame the horizontal position is locked: -1 is the left edge of a square frame, 0 the centre.",
  "pin y": "where on the frame the vertical position is locked: -1 the top edge, 0 the centre, 1 the bottom edge.",
  "curve": "bows the streak into a shallow arc instead of a rule. Positive sags down the screen. Every anamorphic line bends a little.",
  "dash": "breaks the streak into segments with gaps between them. The pattern is seeded, so it holds still across a clip.",
  "crescent": "cuts the ghost with a disc the size of its own body, the way a barrel clips a reflection: 0 leaves it whole, 0.5 takes a third, 0.9 leaves a thin arc. Rotation aims the opening.",
  "crescent feather": "how soft the cut edge is.",
  "ring": "gathers the specks onto a rim instead of filling the disc — the dust caught around a front-element reflection.",
  "ring width": "how wide that rim is, as a fraction of the spread.",
  "spectral": "lets each speck diffract its own colour instead of taking the element's.",
  // light source
  "threshold": "how bright a spot must be to count as a light, relative to the brightest pixel. Raise it when the flare latches onto bright sky or reflections.",
  "dot threshold": "how bright a dot must be to count, relative to the brightest dot in the clip. Lower it if dots drop out.",
  "max lights": "a CAP on how many flares may exist at once, not a target: a single sun with this at 3 still gives one flare.",
  "max dots": "a cap on how many dots become flares.",
  "smoothing": "irons out frame-to-frame jitter in the light's position. In lock mode it also trusts the smooth fitted path over the wobble; 1 follows the fit exactly.",
  "max jump": "how far the light may travel between frames, as a fraction of frame height. Lower it if the flare wanders to nearby lights; raise it if the flare duplicates or drops out on fast moves.",
  "travel": "how much the flare is allowed to move. 1 follows the tracked path exactly, 0 pins it in one spot for the whole clip.",
  "feature px": "size of the patch being matched, in pixels. Big enough to hold something distinctive, small enough not to change shape as the shot moves.",
  "search px": "how far from the predicted spot the tracker looks each frame. This is its speed limit: raise it for fast motion.",
  "hold": "frames a lost light keeps its flare at full strength before fading, so a thin occluder is a flicker-free pass instead of a cut.",
  "fade": "frames a light takes to fade in when found and out when lost.",
  "scene lock": "how much the light is held to the way the picture moves. A sun is at infinity and moves only with the camera, so at 1 the detector may only nudge it and a hop to a rival source cannot drag it. Set 0 for a light that moves on its own — headlights, a torch. A matte with no scene in it is left alone either way.",
  "search radius": "only look for the light inside this circle around the picker's light point (fraction of frame height). 0 searches the whole frame. Set it when a rival source elsewhere keeps stealing the flare; drag the light point to move the ring.",
  // look
  "master": "linked brightness and size multiplier. 1 preserves the preset; 0 turns it off. Base brightness and size remain independent in lens settings.",
  "aspect": "stretches every element horizontally: 1 is spherical, 1.3 to 2 reads as anamorphic.",
  // element settings
  "irregular": "seeded organic unevenness in the shape, so it stops looking computer-perfect.",
  "light mask": "reveal this element only where the light is. Set it to 1 for lens dirt and bloom.",
  "mask scene": "how much the scene's own bright areas reveal it, as opposed to the light's pool. 0 pins the reveal to the light and keeps a lens plate perfectly still; 1 lets a bright sky light it too.",
  "mask floor": "hide the element wherever the light's pool is dimmer than this. Raise it until dirt disappears from the dark parts of frame.",
  "dispersion": "chromatic spread: splits the element into a rainbow along the axis.",
  "dispersion samples": "how many colour steps make up the rainbow; more is smoother and slower.",
  "rotation": "turns the element. With auto-rotate on, this is added to the axis angle.",
  "count": "repeats the element along the axis: a ghost chain.",
  "spread": "how far apart the repeats sit along the axis.",
  "count falloff": "how much dimmer each repeat is than the one before.",
  "count scale step": "how much bigger (above 1) or smaller (below 1) each repeat is than the one before.",
  "completion": "how much of the ring is drawn, in degrees. 360 is closed.",
  "completion feather": "softens the two ends of an open ring.",
  "stretch x": "squashes or stretches this element on its own horizontal axis.",
  "stretch y": "squashes or stretches this element on its own vertical axis.",
  "move x": "a lock: at 0 the element stops following the light horizontally and stays put while the flare travels.",
  "move y": "a lock: at 0 the element stops following the light vertically and stays put while the flare travels.",
  // shape parameters
  "softness": "how gradually the glow fades from its centre.",
  "falloff": "how quickly it dies away with distance.",
  "blades": "number of iris blades, the polygon's sides.",
  "roundness": "curves the aperture outline from a polygon (0) toward a circle (1); independent of edge softness.",
  "edge softness": "softens the polygon's edge.",
  "hollow": "carves out the middle, leaving a ring.",
  "length": "how far it reaches, as a fraction of the frame.",
  "thickness": "how wide it is.",
  "radius": "how far from its centre the ring sits.",
  "angular falloff": "fades the hoop around its circumference, away from the axis.",
  "points": "number of rays.",
  "length jitter": "random variation in ray length, so no two rays match.",
  "size jitter": "random variation in orb size.",
  "illumination": "how strongly the orbs light up as the light approaches them.",
  // trigger rules
  "inner": "inner edge of the trigger region, as a fraction of the frame. The effect ramps between inner and outer.",
  "outer": "outer edge of the trigger region, as a fraction of the frame.",
};
const TRIGGER_TIPS = {
  "brightness": "extra brightness added while triggered.",
  "scale": "extra size added while triggered.",
  "rotation": "extra rotation, in degrees, added while triggered.",
};
const DROPDOWN_TIPS = {
  "mode": "what makes this element react: nothing, the light nearing the frame border, the light nearing frame centre, or the element's own distance to the light.",
  "driven by": "whether the rule measures the light's position or this element's.",
  "falloff": "the shape of the ramp between the region's inner and outer edge.",
  "shape": "the outline used for each spot.",
};
const CHECK_TIPS = {
  "auto-rotate": "add the flare axis angle to the element's rotation so it stays aligned as the light moves.",
  "screen space (lens)": "lock the element to the lens instead of the flare axis: rendered once at frame centre, lit by the strongest light. For dirt and orbs.",
  "fill frame (lens plate)": "map the texture across the whole frame at the frame's aspect: for dirt and droplet plates, not shapes on black.",
};

// A small (i) that shows its text on hover. The bubble is appended to the
// body at a fixed position rather than inside the panel, because the panel
// scrolls and clips: a tooltip that half-disappears is worse than none.
function infoIcon(text) {
  const i = document.createElement("span");
  i.className = "fcore-info";
  i.textContent = "i";
  let tip = null;
  const hide = () => { tip?.remove(); tip = null; };
  const show = () => {
    hide();
    tip = document.createElement("div");
    tip.className = "fcore-tip";
    tip.textContent = text;
    document.body.appendChild(tip);
    const r = i.getBoundingClientRect();
    const w = tip.offsetWidth || 250;
    tip.style.left = `${Math.max(6, Math.min(r.left - 8, window.innerWidth - w - 8))}px`;
    tip.style.top = `${r.bottom + 6}px`;
  };
  i.addEventListener("pointerenter", show);
  i.addEventListener("pointerleave", hide);
  i.addEventListener("pointerdown", (e) => { e.stopPropagation(); e.preventDefault(); });
  return i;
}
function hideAllTips() {
  document.querySelectorAll(".fcore-tip, .fcore-peek").forEach((t) => t.remove());
}

function sliderCol(label, value, [min, max, step], onChange, tip) {
  const wrap = document.createElement("div");
  wrap.className = "fcore-col";
  const lab = document.createElement("label");
  lab.textContent = label;
  const text = tip === undefined ? TIPS[label] : tip;
  if (text) lab.appendChild(infoIcon(text));
  const range = document.createElement("input");
  range.type = "range"; range.min = min; range.max = max; range.step = step;
  const real = Number.isFinite(Number(value)) ? Number(value) : min;
  range.value = Math.min(max, Math.max(min, real));
  const num = document.createElement("input");
  num.type = "number"; num.min = min; num.step = step;
  const fmt = (v) => Number(v).toFixed(step >= 1 ? 0 : Math.max(2, Math.ceil(-Math.log10(step))));
  num.value = fmt(real);
  range.addEventListener("input", () => {
    num.value = fmt(range.value);
    onChange(Number(range.value));
  });
  num.addEventListener("change", () => {
    let v = Number(num.value);
    if (!Number.isFinite(v)) return;
    v = Math.max(min, v);
    num.value = fmt(v);
    range.value = Math.min(max, v);
    onChange(v);
  });
  for (const el of [range, num]) {
    el.addEventListener("pointerdown", (e) => e.stopPropagation());
  }
  wrap.append(lab, range, num);
  return wrap;
}

class FlareEditor {
  constructor(node) {
    this.node = node;
    this.expanded = new Set();
    this.libraryFiles = [];
    this.root = document.createElement("div");
    this.root.className = "fcore";
    this.lastText = null;
    this._pending = null;
    // undo/redo over preset text; a slider drag collapses into one state
    this.history = [];
    this.future = [];
    this._lastQuietPush = 0;
    this.lensOpen = false;
    this.previewIndex = null;
    this.root.tabIndex = 0;
    this.root.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); this.undo(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); e.stopPropagation(); this.redo(); }
    });
    this.fetchLibrary();
    this.build();

    // Watch for the preset changing OUTSIDE the editor — a paste into the
    // raw textarea, a FlarePresetLoader link, a workflow load. Without this
    // the rows go stale and slider closures write into the wrong element.
    this._poll = setInterval(() => {
      if (!document.body.contains(this.root)) return;
      if (this._pending) return; // our own coalesced write is in flight
      if (this.root.contains(document.activeElement)) return; // user mid-edit
      const text = this.widget?.value;
      if (text !== this.lastText) {
        this.lastText = text;
        this.build();
      }
    }, 600);
  }

  destroy() {
    clearInterval(this._poll);
    if (this.node) this.node._fcTriggerPreview = null;
  }

  // Remap the expanded-row set across a structural change so the twirled-
  // open panel stays with ITS element instead of whatever lands on its index.
  remapExpanded(fn) {
    this.expanded = new Set([...this.expanded].map(fn).filter((i) => i >= 0));
  }

  get widget() { return findWidget(this.node, "preset_json"); }

  read() {
    try {
      const document = JSON.parse(this.widget?.value || "{}");
      const preset = activeGroup(document)?.preset || document;
      if (!preset.elements) preset.elements = [];
      if (!preset.schema_version) preset.schema_version = 1;
      if (!preset.global) preset.global = {};
      this.error = null;
      return preset;
    } catch (e) {
      this.error = String(e.message || e);
      return null;
    }
  }

  write(preset, { quiet = false } = {}) {
    const document=sceneDocument(this.node),group=activeGroup(document);
    if(group){group.preset=preset;return this.writeDocument(document,{quiet});}
    return this.writeDocument(preset,{quiet});
  }

  writeDocument(preset, { quiet = false } = {}) {
    const text = JSON.stringify(preset, null, 2);
    this.snapshot(quiet);
    if (this.widget) { this.widget.value = text; this.lastText = text; }
    this.node.setDirtyCanvas(true, false);
  }

  // Push the CURRENT text onto the undo stack before it changes. Quiet
  // (slider) writes within half a second of each other share one state, so
  // a drag undoes in one step instead of a hundred.
  snapshot(quiet) {
    const cur = this.widget?.value;
    if (cur == null) return;
    const now = Date.now();
    if (quiet && now - this._lastQuietPush < 500 && this.history.length) {
      this._lastQuietPush = now;
      return;
    }
    if (this.history[this.history.length - 1] === cur) return;
    this.history.push(cur);
    if (this.history.length > 60) this.history.shift();
    this.future = [];
    this._lastQuietPush = quiet ? now : 0;
  }

  undo() {
    this.flushPending();
    if (!this.history.length || !this.widget) return;
    this.future.push(this.widget.value);
    const text = this.history.pop();
    this.widget.value = text; this.lastText = text;
    this.node.setDirtyCanvas(true, false);
    this.build();
  }

  redo() {
    this.flushPending();
    if (!this.future.length || !this.widget) return;
    this.history.push(this.widget.value);
    const text = this.future.pop();
    this.widget.value = text; this.lastText = text;
    this.node.setDirtyCanvas(true, false);
    this.build();
  }

  // Land any coalesced slider write NOW so a structural edit (or an undo)
  // starts from the latest state instead of the widget's stale text.
  flushPending() {
    if (this._flushTimer) { clearTimeout(this._flushTimer); this._flushTimer = null; }
    if (this._pending) {
      const p = this._pending;
      this._pending = null;
      this.write(p, { quiet: true });
    }
  }

  mutate(fn) {
    this.flushPending();
    const preset = this.read();
    if (!preset) return;
    fn(preset);
    this.write(preset);
    this.build();
  }

  // Slider path: change values without rebuilding the DOM under the cursor.
  // Writes coalesce to one JSON serialize per animation frame — a drag emits
  // ~100 input events/second and stringifying a multi-KB preset per event is
  // what made sliders sticky (and sprayed the undo stack). Sliders write
  // absolute values, so mutating the pending object across events is exact.
  mutateQuiet(fn) {
    const preset = this._pending || this.read();
    if (!preset) return;
    try {
      fn(preset);
    } catch (e) {
      this._pending = null; // preset changed shape under a live control
      this.build();
      return;
    }
    if (!this._pending) {
      this._pending = preset;
      // a timer, not requestAnimationFrame: rAF stalls in a hidden tab and
      // the pending write would sit there until the tab is fronted again
      this._flushTimer = setTimeout(() => {
        this._flushTimer = null;
        if (this._pending) {
          const p = this._pending;
          this._pending = null;
          this.write(p, { quiet: true });
        }
      }, 16);
    }
  }

  async fetchLibrary() {
    try {
      const r = await api.fetchApi("/flarecore/elements");
      this.libraryFiles = (await r.json()).elements || [];
      if (this.expanded.size) this.build();
    } catch { this.libraryFiles = []; }
  }

  mintId(type) {
    return `${type}_${Date.now().toString(36)}${Math.floor(Math.random() * 46656).toString(36)}`;
  }

  addElement(kind, file = null) {
    const elem = JSON.parse(JSON.stringify(ADD_DEFAULTS[kind] || ADD_DEFAULTS.texture));
    elem.id = this.mintId(elem.type);
    // the row's family, fixed at birth and kept through texture swaps
    if (kind !== "texture") elem.slot = CATEGORY_OF[kind] || CATEGORY_OF[elem.label] || "";
    else if (file?.includes("/")) elem.slot = file.split("/")[0];
    if (elem.type === "texture") {
      if (file) {
        elem.params.file = file;
        if (kind === "texture") {
          elem.label = file.split("/").pop().replace(/\.png$/, "").replace(/_/g, " ");
        }
      } else {
        // recipes like lens dirt prefer a file from their own category
        const cat = CATEGORY_OF[elem.label];
        const match = cat && this.libraryFiles.find((f) => f.startsWith(cat + "/"));
        elem.params.file = match || this.libraryFiles[0] || "";
      }
      // "+ add -> library" hands a bare texture a lens_dirt file: the same
      // plate treatment the gallery pick applies, or it renders as a
      // full-frame wash. The lens-dirt recipe already carries these.
      if (kind === "texture") applyLensPlateDefaults(elem, elem.params.file);
    }
    this.mutate((p) => p.elements.push(elem));
  }

  // A row keeps its FAMILY even after a texture is dropped into it: the
  // slot is what the layer is for ("this is my hoop"), not what file it
  // currently holds. Without this, swapping a hoop for a glow texture made
  // that row a glows row forever, and every swapped row ended up offering
  // glows.
  categoryOf(elem) {
    if (elem.slot) return LEGACY_CATEGORY[elem.slot] || elem.slot;
    if (elem.type === "texture" && elem.params?.file?.includes("/")) {
      return elem.params.file.split("/")[0];
    }
    return CATEGORY_OF[elem.label] || CATEGORY_OF[elem.type] || null;
  }

  // The name-click contract: EVERY element's name opens the gallery
  // filtered to its own category. Picking a file swaps a texture element's
  // file, or converts a procedural element into that texture while keeping
  // its position, size, opacity, colour, blur and identity.
  openAlternatives(elem, i, showAll = false) {
    const cat = showAll ? null : this.categoryOf(elem);
    this.fetchLibrary().then(() => {
      const inCat = cat
        ? this.libraryFiles.filter((f) => f.startsWith(cat + "/")).length : 0;
      // never a dead end: an empty family opens the whole library with a note
      const empty = cat && inCat === 0;
      // Name the ROW, not just its family: "veil" living in the glows
      // family is correct but reads as a mismatch unless the header says so.
      const rowName = elem.label || elem.type;
      openGallery(this.libraryFiles, {
        title: empty
          ? `${rowName} — no ${cat.replace(/_/g, " ")} elements yet, showing everything`
          : cat ? `${rowName} — pick a ${cat.replace(/_/g, " ")} element`
                : `${rowName} — all elements`,
        selected: elem.type === "texture" ? (elem.params?.file || null) : null,
        category: empty ? null : cat,
        onShowAll: (cat && !empty)
          ? () => this.openAlternatives(elem, i, true) : null,
      }, (ref) => this.mutate((p) => {
        const e = p.elements[i];
        // remember the family before the file overwrites the evidence
        if (!e.slot) e.slot = this.categoryOf(e) || "";
        if (e.type !== "texture") {
          e.type = "texture";
          e.params = { file: ref, channel: "auto" };
        } else {
          e.params.file = ref;
        }
        e.label = ref.split("/").pop().replace(/\.png$/, "").replace(/_/g, " ");
        applyLensPlateDefaults(e, ref);
      }));
    });
  }

  // Push the previewed element's rule onto the node for the picker to paint.
  // A rule stops previewing when its row collapses or its mode goes to none.
  syncTriggerPreview() {
    const preset = this.read();
    const elem = (this.previewIndex != null && preset)
      ? preset.elements[this.previewIndex] : null;
    const trig = elem?.trigger;
    const live = !!trig && !!trig.mode && trig.mode !== "none"
      && this.expanded.has(this.previewIndex);
    if (!live) this.previewIndex = null;
    this.node._fcTriggerPreview = live
      ? Object.assign({}, TRIGGER_DEFAULTS, trig) : null;
    this.node._fcPicker?.draw();
  }

  build() {
    hideAllTips();
    // Every edit rebuilds the list, which would otherwise throw the view back
    // to the top — press solo on the tenth element and you lose your place.
    // The scroller is .fcore-list; carry its offset across the rebuild.
    const keptScroll = this.root.querySelector(".fcore-list")?.scrollTop ?? 0;
    this.root.textContent = "";
    const preset = this.read();
    const scene=sceneDocument(this.node),group=activeGroup(scene);
    for(const widget of nodeWidgets(this.node))if(SOURCE_FIELDS.has(widget.name)){
      if(group)hideWidget(widget);else if(this.node._fcAdvanced)showWidget(widget);
    }
    if(this.node._fcActiveGroup!==group?.id){
      this.node._fcActiveGroup=group?.id;
      if(group)selectGroupResult(this.node,scene);
    }

    /* toolbar: + add | presets | save… | library | ⚙  (no play button —
       queueing belongs to ComfyUI's own Run) */
    const bar = document.createElement("div");
    bar.className = "fcore-bar";

    const addBtn = document.createElement("button");
    addBtn.className = "fcore-btn accent";
    addBtn.textContent = "+ add";
    addBtn.onclick = (e) => popupMenu(e, ADD_MENU, (kind) => {
      if (kind === "texture") {
        openGallery(this.libraryFiles, { title: "Add element from library" },
          (ref) => this.addElement("texture", ref));
      } else {
        this.addElement(kind);
      }
    });

    const loadBtn = document.createElement("button");
    loadBtn.className = "fcore-btn fcore-preset-button";
    loadBtn.textContent = (preset?.name || preset?.preset_file || "Choose a preset") + " ▾";
    loadBtn.title = "Preset gallery · " + (preset?.name || "Custom flare");
    loadBtn.onclick = async () => {
      this.flushPending();
      loadBtn.disabled=true;
      try {
        const r=await api.fetchApi("/flarecore/presets");
        if(!r.ok)throw new Error("Preset listing failed");
        const d=await r.json();
        const index=d.index?.length ? d.index : (d.presets || []).map(n=>({name:n.replace(/\.json$/,"")}));
        const current=this.read();
        openPresetGallery({
          index, selected:current?.preset_file || current?.name,
          opener:loadBtn,
          previewUrl:name=>api.apiURL(`/flarecore/preset_preview/${encodeURIComponent(name)}`),
          onChoose:async(action,item)=>{
            const rr=await api.fetchApi(`/flarecore/preset/${encodeURIComponent(item.name)}`);
            if(!rr.ok)throw new Error("Preset load failed");
            const dd=await rr.json();
            const incoming=JSON.parse(dd.json);
            if(!Array.isArray(incoming.elements))throw new Error("Invalid preset");
            if(action==="add"){
              this.mutate(p=>{
                for(const element of incoming.elements){
                  element.id=this.mintId(element.type || "elem");p.elements.push(element);
                }
              });
            } else {
              incoming.name=incoming.name || item.title || item.name;
              incoming.preset_file=item.name;
              this.flushPending();this.write(incoming);this.build();
            }
          }
        });
      } catch(err) {
        loadBtn.title="Could not open preset gallery. Restart ComfyUI after updating and retry.";
        console.error("flarecore presets",err);
      } finally {loadBtn.disabled=false;}
    };

    const saveBtn = document.createElement("button");
    saveBtn.className = "fcore-btn";
    saveBtn.textContent = "save…";
    saveBtn.title = "Save this flare group's preset. Save the workflow to retain all groups and source settings.";
    saveBtn.onclick = async () => {
      this.flushPending();
      const name = prompt("Preset name:", this.read()?.name || "my_flare");
      if (!name) return;
      const post = (overwrite) => api.fetchApi("/flarecore/save_preset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, json: JSON.stringify({...this.read(), name}), overwrite }),
      });
      try {
        let d = await (await post(false)).json();
        if (d.exists || d.shipped) {
          const kind = d.shipped ? "a SHIPPED preset" : "an existing preset";
          if (confirm(`'${name}' is ${kind}. Overwrite it?`)) {
            d = await (await post(true)).json();
          }
        }
        saveBtn.textContent = d.saved ? "saved ✓" : (d.error ? "not saved" : "save…");
        if(d.saved)this.mutate(p=>{p.name=name;p.preset_file=d.saved.replace(/\.json$/,"");});
        if (d.error && !d.saved) console.warn("flarecore save:", d.error);
      } catch { saveBtn.textContent = "error"; }
      setTimeout(() => { saveBtn.textContent = "save…"; }, 1800);
    };

    const libBtn = document.createElement("button");
    libBtn.className = "fcore-btn";
    libBtn.textContent = "library";
    libBtn.title = "browse your generated elements; click one to add it to the stack";
    libBtn.onclick = async () => {
      await this.fetchLibrary();
      openGallery(this.libraryFiles, { title: "Element library — click to add" },
        (ref) => this.addElement("texture", ref));
    };

    const advBtn = document.createElement("button");
    advBtn.className = "fcore-btn" + (this.node._fcAdvanced ? " on" : "");
    advBtn.textContent = "⚙";
    advBtn.title = "show the node's inputs (preset JSON, detection, occlusion, blending, seed)";
    advBtn.onclick = () => { setAdvanced(this.node, !this.node._fcAdvanced); this.build(); };

    const undoBtn = document.createElement("button");
    undoBtn.className = "fcore-btn";
    undoBtn.textContent = "↶";
    undoBtn.title = "undo (Ctrl+Z)";
    undoBtn.disabled = !this.history.length;
    undoBtn.onclick = () => this.undo();
    const redoBtn = document.createElement("button");
    redoBtn.className = "fcore-btn";
    redoBtn.textContent = "↷";
    redoBtn.title = "redo (Ctrl+Y)";
    redoBtn.disabled = !this.future.length;
    redoBtn.onclick = () => this.redo();

    bar.append(addBtn, loadBtn, saveBtn, libBtn, undoBtn, redoBtn, advBtn);
    if (this.error) {
      const badge = document.createElement("span");
      badge.className = "fcore-badge";
      badge.textContent = "⚠ invalid JSON";
      bar.appendChild(badge);
    }
    this.root.appendChild(bar);
    this.root.appendChild(this.buildGroups());
    this.root.appendChild(this.buildSourceRow());
    if (!preset) return;

    /* global row: master slider + value + tint swatch */
    const g = preset.global;
    const gRow = document.createElement("div");
    gRow.className = "fcore-global";
    const gLab = document.createElement("label");
    gLab.textContent = "master";
    gLab.appendChild(infoIcon(TIPS["master"]));
    const gRange = document.createElement("input");
    gRange.type = "range"; gRange.min = 0; gRange.max = 3; gRange.step = 0.01;
    gRange.value = g.master ?? 1;
    gRange.setAttribute("aria-label", "Master brightness and size");
    const gNum = document.createElement("input");
    gNum.type = "number"; gNum.min = 0; gNum.max = 3; gNum.step = 0.01;
    gNum.value = Number(gRange.value).toFixed(2);
    gRange.addEventListener("input", () => {
      gNum.value = Number(gRange.value).toFixed(2);
      this.mutateQuiet((p) => { p.global.master = Number(gRange.value); });
    });
    gNum.addEventListener("change", () => {
      const v = Math.min(3, Math.max(0, Number(gNum.value) || 0));
      gNum.value = v.toFixed(2); gRange.value = v;
      this.mutateQuiet((p) => { p.global.master = v; });
    });
    const aLab = document.createElement("label");
    aLab.textContent = "aspect";
    aLab.appendChild(infoIcon(TIPS["aspect"]));
    const aRange = document.createElement("input");
    aRange.type = "range"; aRange.min = 0.25; aRange.max = 3; aRange.step = 0.01;
    aRange.value = g.aspect ?? 1;
    const aNum = document.createElement("input");
    aNum.type = "number"; aNum.min = 0.25; aNum.max = 3; aNum.step = 0.01;
    aNum.value = Number(aRange.value).toFixed(2);
    aRange.addEventListener("input", () => {
      aNum.value = Number(aRange.value).toFixed(2);
      this.mutateQuiet((p) => { p.global.aspect = Number(aRange.value); });
    });
    aNum.addEventListener("change", () => {
      const v = Math.min(3, Math.max(0.25, Number(aNum.value) || 1));
      aNum.value = v.toFixed(2); aRange.value = v;
      this.mutateQuiet((p) => { p.global.aspect = v; });
    });
    const tint = document.createElement("input");
    tint.type = "color";
    tint.className = "fcore-swatch";
    tint.title = "global tint";
    tint.value = colorToHex(g.tint || [1, 1, 1]);
    tint.addEventListener("input", () =>
      this.mutateQuiet((p) => { p.global.tint = hexToColor(tint.value); }));
    for (const el of [gRange, gNum, aRange, aNum, tint]) {
      el.addEventListener("pointerdown", (e) => e.stopPropagation());
    }
    const lensBtn = document.createElement("button");
    lensBtn.className = "fcore-btn" + (this.lensOpen ? " on" : "");
    lensBtn.textContent = this.lensOpen ? "lens ▴" : "lens ▾";
    lensBtn.title = "lens-wide behaviour: chromatic fringe, flicker, edge fade";
    lensBtn.onclick = () => { this.lensOpen = !this.lensOpen; this.build(); };
    gRow.append(gLab, gRange, gNum, aLab, aRange, aNum, tint, lensBtn);
    this.root.appendChild(gRow);

    if (this.lensOpen) {
      const lens = document.createElement("div");
      lens.className = "fcore-global lens";
      const gslider = (label, key, spec, fallback) => {
        const col = sliderCol(label, g[key] ?? fallback, spec,
          (v) => this.mutateQuiet((p) => { p.global[key] = v; }));
        col.style.flex = "1";
        lens.appendChild(col);
      };
      gslider("fringe", "fringe", [0, 1, 0.01], 0);
      gslider("base brightness", "intensity", [0, 3, 0.01], 1);
      gslider("base size", "scale", [.01, 3, .01], 1);
      gslider("flicker", "flicker_amount", [0, 1, 0.01], 0);
      gslider("flicker speed", "flicker_speed", [0, 5, 0.05], 1);
      gslider("edge fade start", "edge_fade_start", [0, 2, 0.01], 0);
      gslider("edge fade range", "edge_fade_range", [0, 2, 0.01], 0);
      this.root.appendChild(lens);
    }

    /* element rows */
    const list = document.createElement("div");
    list.className = "fcore-list";
    this.anySolo = preset.elements.some((e) => e.solo);
    preset.elements.forEach((elem, i) => list.appendChild(this.buildRow(elem, i)));
    this.syncTriggerPreview();
    if (!preset.elements.length) {
      const empty = document.createElement("div");
      empty.style.cssText = "color:#777;text-align:center;padding:16px;";
      empty.textContent = "no elements — + add, or pick from the library";
      list.appendChild(empty);
    }
    this.root.appendChild(list);
    // only now: a detached element has no scroll height, so assigning
    // scrollTop before the append silently clamps to 0
    if (keptScroll) {
      list.scrollTop = keptScroll;
      requestAnimationFrame(() => { list.scrollTop = keptScroll; });
    }
  }

  changeGroups(change) {
    this.flushPending();
    if(!sceneDocument(this.node))return;
    const document=ensureGroups(sceneDocument(this.node),this.node);
    change(document);
    this.writeDocument(document);this.expanded.clear();this.previewIndex=null;
    selectGroupResult(this.node,document);this.build();
  }

  buildGroups() {
    const row=document.createElement('div');row.className='fcore-groups';
    const scene=sceneDocument(this.node),selected=activeGroup(scene);
    if(!scene){row.textContent='Fix the preset JSON before editing flare groups.';return row;}
    const title=document.createElement('div');title.className='fcore-group-title';
    title.textContent='FLARE GROUPS · settings below apply only to the selected group';row.append(title);
    if(scene.groups && (!Array.isArray(scene.groups) || scene.groups.some(g=>!g?.preset))){
      row.textContent='Invalid flare groups. Correct the scene JSON or use Undo.';return row;
    }
    const groups=scene?.groups || [{id:'single',name:'Flare 1',enabled:true}];
    for(const group of groups){
      const button=document.createElement('button');button.className='fcore-btn'+(!selected||selected.id===group.id?' active':'');
      button.textContent=(group.enabled===false?'○ ':'● ')+group.name;button.setAttribute('aria-pressed',String(!selected||selected.id===group.id));
      button.onclick=()=>{if(selected && selected.id!==group.id)this.changeGroups(d=>{d.active_group=group.id;});};row.append(button);
    }
    const add=document.createElement('button');add.className='fcore-btn';add.textContent='+ Flare';add.disabled=groups.length>=16;
    add.onclick=()=>this.changeGroups(d=>{
      const id=newGroupId(d);d.groups.push({id,name:`Flare ${d.groups.length+1}`,enabled:true,
        preset:{schema_version:1,name:'Choose a preset',global:{},elements:[]},
        source:{...clone(activeGroup(d).source),position_mode:'manual',use_lights_input:false,light_x:.7,light_y:.3}});
      d.active_group=id;
    });
    const duplicate=document.createElement('button');duplicate.className='fcore-btn';duplicate.textContent='Duplicate flare';duplicate.disabled=groups.length>=16;
    duplicate.onclick=()=>this.changeGroups(d=>{
      const copy=clone(activeGroup(d));copy.id=newGroupId(d);copy.name+=' copy';
      for(const element of copy.preset.elements || [])element.id=this.mintId(element.type || 'elem');
      d.groups.push(copy);d.active_group=copy.id;
    });
    const rename=document.createElement('button');rename.className='fcore-btn';rename.textContent='Rename';
    rename.onclick=()=>{const name=prompt('Flare group name:',selected?.name || 'Flare 1');if(name?.trim())this.changeGroups(d=>{activeGroup(d).name=name.trim();});};
    const enabled=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=selected?.enabled!==false;
    check.onchange=()=>this.changeGroups(d=>{activeGroup(d).enabled=check.checked;});enabled.append(check,' Enabled');
    const remove=document.createElement('button');remove.className='fcore-btn';remove.textContent='Remove flare';remove.disabled=groups.length<=1;
    remove.title='Remove the selected group. Undo restores it.';
    remove.onclick=()=>this.changeGroups(d=>{d.groups=d.groups.filter(g=>g.id!==activeGroup(d).id);d.active_group=d.groups[0].id;});
    row.append(add,duplicate,rename,enabled,remove);return row;
  }

  // The light-source row: one dropdown, and only the controls that mode
  // actually uses. Bound to the NODE's widgets, not the preset.
  buildSourceRow() {
    const row = document.createElement("div");
    row.className = "fcore-global src";
    const mode = getStr(this.node, "position_mode") || "manual";

    const group=activeGroup(sceneDocument(this.node));
    const connected=this.node.inputs?.some(input=>input.name === "lights" && input.link != null);
    if(group && connected){
      const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';
      check.checked=group.source?.use_lights_input===true;check.onchange=()=>{setGroupSource(this.node,'use_lights_input',check.checked);this.build();};
      label.append(check,' Use connected lights for this group');row.append(label);
    }
    if (connected && (!group || group.source?.use_lights_input===true)) {
      const message=document.createElement("div");message.className="fcore-hint";
      message.textContent="External lights connected. Positions come from that input; disconnect it to use the source controls. Your source settings are preserved.";
      row.append(message);return row;
    }

    const lab = document.createElement("label");
    lab.textContent = "light source";
    lab.appendChild(infoIcon("Choose how the source is positioned. Place or Detect for stills; tracking, camera motion, features or a drawn path for clips. Source tuning contains only settings used by the chosen method."));
    const sel = document.createElement("select");
    sel.className = "fcore-src-sel";
    sel.ariaLabel = "Light source";
    for (const [value, text] of POSITION_MODES) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = text;
      sel.appendChild(opt);
    }
    sel.value = sourceFamily(mode);
    sel.addEventListener("pointerdown", (e) => e.stopPropagation());
    sel.onchange = () => {
      setStr(this.node, "position_mode", sel.value);
      this.node.setDirtyCanvas(true, false);
      this.node._fcPicker?.draw();
      this.build();
    };
    row.append(lab, sel);

    const chooseMode = value => {
      // Variants share the existing serialized modes: no preset migration.
      setStr(this.node, "position_mode", value);
      this.node.setDirtyCanvas(true, false); this.node._fcPicker?.draw(); this.build();
    };
    if (sourceFamily(mode) === "detect") {
      const offset = document.createElement("label");
      const check = document.createElement("input");check.type="checkbox";
      check.checked=mode === "detect_with_manual_offset";
      check.onchange=()=>chooseMode(check.checked ? "detect_with_manual_offset" : "detect");
      offset.append(check, " Offset from picker");row.append(offset);
    }
    if (sourceFamily(mode) === "track") {
      const method = document.createElement("select");method.className="fcore-src-sel";
      method.ariaLabel="Tracking method";
      for (const [value,label] of [["track","Match between frames"],["lock","Solve entire clip"],["track_dots","Track a dot matte"]]) {
        const option=document.createElement("option");option.value=value;option.textContent=label;method.append(option);
      }
      method.value=mode;method.onchange=()=>chooseMode(method.value);row.append(method);
    }
    if (Object.keys(MODE_DEFAULTS[mode] || {}).length) {
      const reset=document.createElement("button");reset.className="fcore-btn";
      reset.textContent="Recommended settings";
      reset.title="Replace this source mode's tuning with its recommended starting values.";
      reset.onclick=()=>{
        for(const [name,value] of Object.entries(MODE_DEFAULTS[mode])) setVal(this.node,name,value);
        this.node.setDirtyCanvas(true,false);this.build();
      };row.append(reset);
    }

    const tuning=document.createElement("details");tuning.className="fcore-source-tuning";
    tuning.open=this.sourceTuningOpen===true;
    tuning.ontoggle=()=>{this.sourceTuningOpen=tuning.open;};
    const tuningTitle=document.createElement("summary");tuningTitle.textContent="Source tuning";
    const tuningGrid=document.createElement("div");tuningGrid.className="fcore-source-grid";
    tuning.append(tuningTitle,tuningGrid);row.append(tuning);
    const nodeSlider = (label, widget, spec) => {
      const col = sliderCol(label, getVal(this.node, widget, spec[0]), spec,
        (v) => {
          setVal(this.node, widget, v);
          this.node.setDirtyCanvas(true, false);
        });
      col.style.flex = "1";
      tuningGrid.appendChild(col);
    };

    if(group){
      nodeSlider('light X','light_x',[-1,2,.001]);nodeSlider('light Y','light_y',[-1,2,.001]);
      nodeSlider('anchor X','flare_x',[-1,2,.001]);nodeSlider('anchor Y','flare_y',[-1,2,.001]);
    }

    if (mode === "detect" || mode === "detect_with_manual_offset") {
      nodeSlider("threshold", "detect_threshold", [0, 1, 0.01]);
      nodeSlider("max lights", "detect_max_lights", [1, 16, 1]);
      nodeSlider("search radius", "search_radius", [0, 1, 0.01]);
      nodeSlider("smoothing", "track_smoothing", [0, 0.98, 0.01]);
      nodeSlider("scene lock", "scene_lock", [0, 1, 0.01]);
      nodeSlider("travel", "light_travel", [0, 1, 0.01]);
    } else if (mode === "track" || mode === "track_dots" || mode === "lock") {
      nodeSlider(mode === "track_dots" ? "dot threshold" : "threshold",
        "detect_threshold", [0, 1, 0.01]);
      nodeSlider(mode === "track_dots" ? "max dots" : "max lights",
        "detect_max_lights", [1, 16, 1]);
      nodeSlider("smoothing", "track_smoothing", [0, 0.98, 0.01]);
      nodeSlider("max jump", "track_max_jump", [0.01, 0.5, 0.01]);
      nodeSlider("travel", "light_travel", [0, 1, 0.01]);
      if (mode !== "lock") nodeSlider("scene lock", "scene_lock", [0, 1, 0.01]);
      if (mode !== "lock") {
        nodeSlider("hold", "track_hold", [0, 60, 1]);
        nodeSlider("fade", "track_fade", [1, 60, 1]);
      }
      nodeSlider("search radius", "search_radius", [0, 1, 0.01]);
    } else if (mode === "follow") {
      nodeSlider("smoothing", "track_smoothing", [0, 0.98, 0.01]);
      nodeSlider("travel", "light_travel", [0, 1, 0.01]);
    } else if (mode === "point_track") {
      const marks = parsePath(getStr(this.node, "track_points"));
      const count = document.createElement("label");
      count.textContent = marks.length === 0 ? "no tracker placed"
        : marks.length === 1 ? "1 tracker — position only"
        : "2 trackers — position, rotation, scale";
      const clear = document.createElement("button");
      clear.className = "fcore-btn";
      clear.textContent = "clear";
      clear.disabled = !marks.length;
      clear.onclick = () => {
        setStr(this.node, "track_points", "");
        this.node.setDirtyCanvas(true, false);
        this.build();
      };
      const col = document.createElement("div");
      col.className = "fcore-col";
      col.append(count, clear);
      row.appendChild(col);
      nodeSlider("feature px", "track_feature", [8, 128, 2]);
      nodeSlider("search px", "track_search", [8, 256, 2]);
      nodeSlider("smoothing", "track_smoothing", [0, 0.98, 0.01]);
      nodeSlider("travel", "light_travel", [0, 1, 0.01]);
    } else if (mode === "path") {
      const pts = parsePath(getStr(this.node, "light_path"));
      const aps = parsePath(getStr(this.node, "anchor_path"));
      const count = document.createElement("label");
      count.textContent = `${pts.length} point${pts.length === 1 ? "" : "s"}`
        + (aps.length ? ` · anchor on its own path` : "");
      const clear = document.createElement("button");
      clear.className = "fcore-btn";
      clear.textContent = "clear path";
      clear.disabled = !pts.length && !aps.length;
      clear.onclick = () => {
        setStr(this.node, "light_path", "");
        setStr(this.node, "anchor_path", "");
        this.node.setDirtyCanvas(true, false);
        this.node._fcPicker?.draw();
        this.build();
      };
      row.append(count, clear);
    }

    const visibilityCol = document.createElement("label");
    visibilityCol.className = "fcore-col";
    visibilityCol.textContent = "Obstruction";
    const visibilitySelect = document.createElement("select");
    visibilitySelect.className = "fcore-src-sel";
    for (const [value, title] of [["hybrid", "Image + depth (recommended)"],
      ["image", "Image visibility"], ["depth", "Depth only (legacy)"], ["off", "Off"]]) {
      const option = document.createElement("option"); option.value = value; option.textContent = title;
      visibilitySelect.append(option);
    }
    visibilitySelect.value = getStr(this.node, "visibility_mode") || "hybrid";
    visibilitySelect.title = "Measures visible source energy, not tracker confidence. Image visibility needs a clear reference in the clip and also responds to exposure changes.";
    visibilitySelect.onchange = () => { setStr(this.node, "visibility_mode", visibilitySelect.value); this.node.setDirtyCanvas(true, false); };
    visibilityCol.append(visibilitySelect); tuningGrid.append(visibilityCol);
    nodeSlider("source radius", "occlusion_radius", [.001, .15, .001]);
    nodeSlider("visibility smoothing", "occlusion_smooth", [0, 1, .01]);
    if(group){
      const advanced=document.createElement('details');advanced.className='fcore-source-tuning';
      const heading=document.createElement('summary');heading.textContent='Group depth, colour and seed';advanced.append(heading);
      const grid=document.createElement('div');grid.className='fcore-source-grid';advanced.append(grid);row.append(advanced);
      for(const [label,name,spec,fallback] of [
        ['light depth','light_depth',[0,1,.01],.1],['depth blur','depth_blur',[0,.1,.001],0],
        ['depth smoothing','depth_temporal_smooth',[0,1,.01],0],['scene colour','scene_color',[0,1,.01],0],
        ['mask falloff','mask_falloff',[.01,2,.01],.35],['seed','seed',[0,2147483647,1],0]]){
        grid.append(sliderCol(label,getVal(this.node,name,fallback),spec,v=>setVal(this.node,name,v)));
      }
      const invert=document.createElement('label'),check=document.createElement('input');check.type='checkbox';
      check.checked=!!getVal(this.node,'invert_depth',0);check.onchange=()=>setGroupSource(this.node,'invert_depth',check.checked);
      invert.append(check,' Invert depth');grid.append(invert);
      const normalise=document.createElement('select');normalise.className='fcore-src-sel';normalise.setAttribute('aria-label','Group depth normalisation');
      for(const value of ['as_is','per_frame','per_batch']){const option=document.createElement('option');option.value=value;option.textContent=value.replaceAll('_',' ');normalise.append(option);}
      normalise.value=getStr(this.node,'depth_normalize') || 'as_is';normalise.onchange=()=>setStr(this.node,'depth_normalize',normalise.value);grid.append(normalise);
    }
    if (this.node._fcSourceStatus) {
      const status=document.createElement("div");status.className="fcore-hint";
      status.setAttribute("role","status");status.textContent=this.node._fcSourceStatus;
      tuningGrid.append(status);
    }

    // After a render in any tracked mode the light's path is known. Baking
    // it turns the track into a drawn path the picker can edit point by
    // point -- fix the one frame the tracker got wrong instead of retuning.
    const solved = Array.isArray(this.node._fcTrack)
      ? this.node._fcTrack.filter((q) => Array.isArray(q)) : [];
    if (solved.length > 1 && !["manual", "path"].includes(mode)) {
      const bake = document.createElement("button");
      bake.className = "fcore-btn accent fcore-bake";
      bake.textContent = "bake to path";
      bake.title = "turn the light path from the last render into an editable motion path (switches to path mode; drag or shift-click points to fix it)";
      bake.onclick = () => {
        // Every frame becomes a knot: the path samples each segment at
        // t = 0, so it reproduces the track exactly and dragging one point
        // fixes one frame. A couple of dozen knots read as a smooth curve
        // instead -- fine motion gone, the timing bent. A two-tracker solve
        // keeps its anchor on a path of its own, so the axis keeps the
        // pair's rotation and scale.
        const pts = solved.map((q) => [q[0], q[1]]);
        const aps = solved.every((q) => q[2] != null && q[3] != null)
          ? solved.map((q) => [q[2], q[3]]) : [];
        setStr(this.node, "light_path", formatPath(pts));
        setStr(this.node, "anchor_path", aps.length ? formatPath(aps) : "");
        setStr(this.node, "position_mode", "path");
        this.node.setDirtyCanvas(true, false);
        this.node._fcPicker?.draw();
        this.build();
      };
      row.appendChild(bake);
    }

    const hint = document.createElement("div");
    hint.className = "fcore-hint";
    hint.textContent = MODE_HINT[mode] || "";
    row.appendChild(hint);
    return row;
  }

  chipFor(elem) {
    const chip = document.createElement("div");
    chip.className = "fcore-chip";
    if (elem.type === "texture" && elem.params?.file) {
      const img = document.createElement("img");
      img.src = elementThumbUrl(elem.params.file);
      chip.appendChild(img);
    } else {
      const ico = document.createElement("span");
      ico.className = `ico ico-${elem.type}`;
      chip.appendChild(ico);
    }
    // Hover: the element rendered SOLO, as configured -- colour, scale,
    // count, blur. A texture's file is not what it looks like once tinted
    // and scaled, and a procedural element has no file at all, so the
    // server renders it. Cached by content so a second hover is free.
    let peek = null, timer = null, token = 0;
    const hide = () => {
      clearTimeout(timer); timer = null; token++;
      peek?.remove(); peek = null;
    };
    chip.addEventListener("pointerenter", () => {
      hide();
      const mine = ++token;
      timer = setTimeout(async () => {
        peek = document.createElement("div");
        peek.className = "fcore-peek";
        const big = document.createElement("img");
        const cap = document.createElement("span");
        cap.textContent = "rendering…";
        peek.append(big, cap);
        document.body.appendChild(peek);
        const rr = chip.getBoundingClientRect();
        peek.style.left = `${Math.min(rr.right + 8, window.innerWidth - 276)}px`;
        peek.style.top = `${Math.max(6, Math.min(rr.top - 40, window.innerHeight - 190))}px`;
        try {
          const url = await elementPreviewUrl(elem, this.read().global || {});
          if (mine !== token || !peek) return;
          big.src = url;
          cap.textContent = (elem.label || elem.type)
            + (elem.type === "texture" && elem.params?.file ? ` — ${elem.params.file}` : "");
        } catch (e) {
          if (mine === token && peek) cap.textContent = "no preview";
        }
      }, 120);
    });
    chip.addEventListener("pointerleave", hide);
    return chip;
  }

  buildRow(elem, i) {
    const row = document.createElement("div");
    row.className = "fcore-row" + (elem.enabled === false ? " off" : "")
      + (this.anySolo && !elem.solo ? " dim" : "")
      // a red edge marks a row that reacts to the light's position, so a
      // preset's rule-driven elements are visible at a glance
      + (elem.trigger && elem.trigger.mode && elem.trigger.mode !== "none"
        ? " trig" : "");

    const head = document.createElement("div");
    head.className = "fcore-head";

    const en = document.createElement("input");
    en.type = "checkbox";
    en.checked = elem.enabled !== false;
    en.addEventListener("pointerdown", (e) => e.stopPropagation());
    en.onchange = () => this.mutate((p) => { p.elements[i].enabled = en.checked; });

    const chip = this.chipFor(elem);

    const toggle = () => {
      this.expanded.has(i) ? this.expanded.delete(i) : this.expanded.add(i);
      this.build();
    };

    // The NAME (and chip) of EVERY element opens the gallery filtered to
    // its own category — alternatives for this look, generated in the
    // forge. Settings live behind the chevron only.
    const name = document.createElement("span");
    name.className = "fcore-name";
    name.textContent = elem.label || elem.type;
    const cat = this.categoryOf(elem);
    name.title = "click for alternatives" + (cat ? ` (${cat.replace(/_/g, " ")})` : "");
    const pick = () => this.openAlternatives(elem, i);
    name.onclick = pick;
    chip.style.cursor = "pointer";
    chip.onclick = pick;
    const rename = () => {
      const v = prompt("Rename element:", elem.label || elem.type);
      if (v != null && v.trim()) this.mutate((p) => { p.elements[i].label = v.trim(); });
    };
    name.ondblclick = (e) => { e.stopPropagation(); rename(); };

    head.append(en, chip, name);
    head.appendChild(sliderCol("pos", elem.offset ?? 0, [-1, 3, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].offset = v; })));
    head.appendChild(sliderCol("size", elem.scale ?? 0.5, [0.01, 2.5, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].scale = v; })));
    head.appendChild(sliderCol("opac", elem.intensity ?? 1, [0, 1, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].intensity = v; })));
    head.appendChild(sliderCol("blur", elem.blur ?? 0, [0, 1, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].blur = v; })));

    // recolor lives on the face of the row, not buried in a submenu
    const col = document.createElement("input");
    col.type = "color";
    col.className = "fcore-swatch";
    col.title = "element color (use luminance channel on textures for a full recolor)";
    col.value = colorToHex(elem.color || [1, 1, 1]);
    col.addEventListener("pointerdown", (e) => e.stopPropagation());
    col.addEventListener("input", () =>
      this.mutateQuiet((p) => { p.elements[i].color = hexToColor(col.value); }));
    head.appendChild(col);

    const acts = document.createElement("div");
    acts.className = "fcore-acts";
    const mk = (txt, title, fn, html = false) => {
      const b = document.createElement("button");
      b.className = "fcore-mini";
      if (html) b.innerHTML = txt;
      else b.textContent = txt;
      b.title = title;
      b.onclick = fn;
      acts.appendChild(b);
      return b;
    };
    mk(this.expanded.has(i) ? "▴" : "▾", "settings", toggle);
    const solo = mk("S", "solo (render only soloed elements)", () =>
      this.mutate((p) => { p.elements[i].solo = !p.elements[i].solo; }));
    if (elem.solo) solo.classList.add("on");
    mk("⋯", "more: rename, copy / paste settings", (e) => popupMenu(e, [
      ["rename", "rename"], ["copy settings", "copy"],
      [clipboardElem ? "paste settings" : "paste settings (nothing copied)", "paste"],
    ], (a) => {
      if (a === "rename") rename();
      else if (a === "copy") {
        clipboardElem = JSON.parse(JSON.stringify(elem));
        delete clipboardElem.id; delete clipboardElem.label; delete clipboardElem.slot;
      } else if (a === "paste" && clipboardElem) {
        this.mutate((p) => {
          const keep = p.elements[i];
          const merged = JSON.parse(JSON.stringify(clipboardElem));
          merged.id = keep.id; merged.label = keep.label; merged.slot = keep.slot;
          p.elements[i] = merged;
        });
      }
    }));
    mk("⧉", "duplicate", () => {
      this.remapExpanded((e) => (e > i ? e + 1 : e));
      this.mutate((p) => {
        const copy = JSON.parse(JSON.stringify(p.elements[i]));
        copy.id = this.mintId(copy.type);
        p.elements.splice(i + 1, 0, copy);
      });
    });
    mk("↑", "move up", () => { if (i > 0) {
      this.remapExpanded((e) => (e === i ? i - 1 : e === i - 1 ? i : e));
      this.mutate((p) => p.elements.splice(i - 1, 0, p.elements.splice(i, 1)[0]));
    } });
    mk("↓", "move down", () => {
      this.remapExpanded((e) => (e === i ? i + 1 : e === i + 1 ? i : e));
      this.mutate((p) => {
        if (i < p.elements.length - 1) p.elements.splice(i + 1, 0, p.elements.splice(i, 1)[0]);
      });
    });
    const TRASH_SVG =
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
      '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 ' +
      '0 1-2 2H7a2 2 0 0 1-2-2V6m5 5v6m4-6v6"/></svg>';
    const del = mk(TRASH_SVG, "delete", () => {
      this.remapExpanded((e) => (e === i ? -1 : e > i ? e - 1 : e));
      this.mutate((p) => p.elements.splice(i, 1));
    }, true);
    del.style.color = "#c96a6a";
    head.appendChild(acts);

    row.appendChild(head);
    if (this.expanded.has(i)) row.appendChild(this.buildAdvanced(elem, i));
    return row;
  }

  buildAdvanced(elem, i) {
    const adv = document.createElement("div");
    adv.className = "fcore-adv";
    const set = (k, v) => this.mutateQuiet((p) => { p.elements[i][k] = v; });
    const setParam = (k, v) => this.mutateQuiet((p) => {
      if (!p.elements[i].params) p.elements[i].params = {};
      p.elements[i].params[k] = v;
    });

    this.motionPanelStates ??= new Map();
    const motionKey = elem.id || i;
    if (!this.motionPanelStates.has(motionKey)) this.motionPanelStates.set(motionKey, {});
    const panelState = this.motionPanelStates.get(motionKey);
    panelState.sections ??= new Set(["Shape & appearance"]);
    const section = (title) => {
      const details = document.createElement("details");
      details.className = "fcore-settings";
      details.open = panelState.sections.has(title);
      details.ontoggle = () => {
        if (details.open) panelState.sections.add(title);
        else panelState.sections.delete(title);
      };
      const summary = document.createElement("summary");
      summary.textContent = title;
      const content = document.createElement("div");
      content.className = "fcore-settings-grid";
      details.append(summary, content); adv.append(details);
      return content;
    };
    const appearance = section("Shape & appearance");
    const placement = section("Position & orientation");
    const copies = section("Repeated elements");
    const visibility = section("Masking & lens space");
    const response = section("Optical response");
    response.appendChild(createMotionPanel(elem, motion => this.mutate(p => {
      p.elements[i].motion = motion;
    }), undefined, this.motionPanelStates.get(motionKey)));

    const dropdown = (label, value, options, onPick) => {
      const wrap = document.createElement("div");
      wrap.className = "fcore-col";
      const l = document.createElement("label");
      l.textContent = label;
      if (DROPDOWN_TIPS[label]) l.appendChild(infoIcon(DROPDOWN_TIPS[label]));
      const sel = document.createElement("select");
      for (const o of options) {
        const opt = document.createElement("option");
        opt.value = o; opt.textContent = o;
        sel.appendChild(opt);
      }
      sel.value = value;
      sel.addEventListener("pointerdown", (e) => e.stopPropagation());
      sel.onchange = () => onPick(sel.value);
      wrap.append(l, sel);
      return wrap;
    };

    if (elem.type === "texture") {
      appearance.appendChild(dropdown("channel (luminance = full recolor)",
        elem.params?.channel || "auto",
        ["auto", "rgb", "luminance"], (v) => setParam("channel", v)));
    }

    const checkbox = (label, checked, onChange) => {
      const wrap = document.createElement("div");
      wrap.className = "fcore-col";
      const l = document.createElement("label");
      l.textContent = label;
      if (CHECK_TIPS[label]) l.appendChild(infoIcon(CHECK_TIPS[label]));
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.addEventListener("pointerdown", (e) => e.stopPropagation());
      cb.onchange = () => onChange(cb.checked);
      wrap.append(l, cb);
      return wrap;
    };
    placement.appendChild(checkbox("auto-rotate", elem.auto_rotate !== false,
      (v) => set("auto_rotate", v)));
    visibility.appendChild(checkbox("screen space (lens)", elem.screen_space === true,
      (v) => { set("screen_space", v); this.flushPending(); this.build(); }));
    visibility.appendChild(checkbox("fill frame (lens plate)", elem.fill_frame === true,
      (v) => set("fill_frame", v)));

    for (const [key, spec] of Object.entries(COMMON_SPECS)) {
      const group = key === "rotation" ? placement
        : ["count", "spread", "count_falloff", "count_scale_step"].includes(key) ? copies
        : ["light_mask", "mask_scene", "mask_floor"].includes(key) ? visibility : appearance;
      group.appendChild(sliderCol(key.replace(/_/g, " "),
        elem[key] ?? COMMON_DEFAULTS[key], spec,
        (v) => set(key, spec[2] >= 1 ? Math.round(v) : v)));
    }
    placement.appendChild(sliderCol("stretch x", elem.stretch?.[0] ?? 1, [0.1, 4, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].stretch || [1, 1];
        p.elements[i].stretch = [v, s[1]];
      })));
    placement.appendChild(sliderCol("stretch y", elem.stretch?.[1] ?? 1, [0.1, 4, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].stretch || [1, 1];
        p.elements[i].stretch = [s[0], v];
      })));

    placement.appendChild(sliderCol("move x", elem.move?.[0] ?? 1, [0, 1, 0.01],
      (v) => this.mutateQuiet((p) => {
        const m = p.elements[i].move || [1, 1];
        p.elements[i].move = [v, m[1]];
      })));
    placement.appendChild(sliderCol("move y", elem.move?.[1] ?? 1, [0, 1, 0.01],
      (v) => this.mutateQuiet((p) => {
        const m = p.elements[i].move || [1, 1];
        p.elements[i].move = [m[0], v];
      })));

    // A screen-space nudge: unlike `offset` it does not swing with the
    // flare axis, so a companion ghost stays a fixed drop below the source.
    placement.appendChild(sliderCol("shift x", elem.shift?.[0] ?? 0, [-2, 2, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].shift || [0, 0];
        p.elements[i].shift = [v, s[1]];
      })));
    placement.appendChild(sliderCol("shift y", elem.shift?.[1] ?? 0, [-2, 2, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].shift || [0, 0];
        p.elements[i].shift = [s[0], v];
      })));

    // Lock one axis to the frame and the other keeps following the light:
    // the hotspot that rides the top edge above the source.
    for (const [axis, idx] of [["x", 0], ["y", 1]]) {
      const at = Array.isArray(elem.pin) ? elem.pin[idx] : null;
      const setPin = (value, rebuild) => {
        this.mutateQuiet((p) => {
          const q = Array.isArray(p.elements[i].pin)
            ? p.elements[i].pin.slice() : [null, null];
          q[idx] = value;
          p.elements[i].pin = q;
        });
        if (rebuild) { this.flushPending(); this.build(); }
      };
      placement.appendChild(checkbox(`pin ${axis} to frame`, at != null,
        (on) => setPin(on ? (idx === 1 ? -1 : 0) : null, true)));
      if (at != null) {
        placement.appendChild(sliderCol(`pin ${axis}`, at, [-2, 2, 0.01],
          (v) => setPin(v, false)));
      }
    }

    if (elem.type === "spectral") {
      appearance.appendChild(dropdown("shape", elem.params?.shape || "ring",
        ["ring", "iris"], (v) => setParam("shape", v)));
    }
    if (elem.type === "orbs") {
      appearance.appendChild(dropdown("shape", elem.params?.shape || "disc",
        ["disc", "polygon"], (v) => setParam("shape", v)));
    }
    for (const [key, spec] of Object.entries(PARAM_SPECS[elem.type] || {})) {
      appearance.appendChild(sliderCol(key.replace(/_/g, " "),
        elem.params?.[key] ?? PARAM_FALLBACKS[elem.type]?.[key], spec,
        (v) => setParam(key, spec[2] >= 1 ? Math.round(v) : v)));
    }

    if (elem.screen_space || elem.type === "orbs") {
      visibility.appendChild(dropdown("lens illumination", elem.screen_blend || "strongest",
        ["strongest", "all"], v => set("screen_blend", v)));
    }

    // Old files must not silently change appearance or hide an active rule.
    // New work uses optical response; compatibility is visible only if needed.
    if (elem.trigger && elem.trigger.mode !== "none") {
      const notice=document.createElement("div");notice.className="fcore-hint";
      notice.textContent="This element has an older trigger rule. It is still rendered for compatibility. Use Optical response for new animation. ";
      const disable=document.createElement("button");disable.className="fcore-btn";
      disable.textContent="Remove old rule";
      disable.onclick=()=>this.mutate(p=>{p.elements[i].trigger=null;});
      notice.append(disable);adv.append(notice);
    }
    return adv;
  }
}

/* ------------------------------------------------------------ forge panel */

// A default worth suggesting; the toggle decides whether it is appended.
const EXTRA_STYLE_SUGGESTION =
  "photographed on vintage anamorphic glass, subtle blue-green tint, " +
  "gentle halation, organic imperfection";

// One panel = the whole prompt side of the forge: category -> element
// dropdowns, the bank prompt exposed for editing, and the extra-style
// suggestion behind a toggle. It writes the node's real widgets
// (element / custom_prompt / extra_style), which stay hidden underneath.
// Hand the panel the node height left under it, the way fitEditor does for
// the stack editor.
function fitForge(node) {
  const w = findWidget(node, "forge_panel");
  if (!w) return;
  // w.y is only filled in once the canvas has laid the node out, and the
  // panel has to be the right height before that (on load, and on a resize
  // in a tab that has not drawn yet). Every other widget on this node is
  // hidden, so the offset is just the title bar — fall back to that.
  const laid = Number.isFinite(w.y) && w.y > 0 ? w.y
    : (Number.isFinite(w.last_y) && w.last_y > 0 ? w.last_y : 34);
  node._fcForgeH = Math.max(150, node.size[1] - laid - 12);
}

function setupForgePanel(nodeType) {
  const onNodeCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    onNodeCreated?.apply(this, arguments);
    const node = this;
    injectCSS();
    for (const w of node.widgets || []) hideWidget(w);

    const root = document.createElement("div");
    root.className = "fcore fcore-forge";

    const header=document.createElement("header");
    const title=document.createElement("h3");title.textContent="Element Forge";
    const intro=document.createElement("p");intro.className="forge-intro";
    intro.textContent="Define an element → generate → prepare → save to your library.";
    header.append(title,intro);
    const selectionCard=document.createElement("section");selectionCard.className="forge-card";
    const selectionTitle=document.createElement("div");selectionTitle.className="forge-card-title";selectionTitle.textContent="01 / CHOOSE AN ELEMENT";

    const selRow = document.createElement("div");
    selRow.className = "rowline";
    const catSel = document.createElement("select");
    catSel.className = "fcore-src-sel";
    catSel.style.flex = "0 0 34px";
    const elemSel = document.createElement("select");
    elemSel.className = "fcore-src-sel";
    elemSel.style.flex = "0 0 34px";
    const categoryLabel=document.createElement("label");categoryLabel.append("Family",catSel);
    const elementLabel=document.createElement("label");elementLabel.append("Element",elemSel);
    selRow.append(categoryLabel,elementLabel);
    selectionCard.append(selectionTitle,selRow);
    const selectionHint=document.createElement("p");selectionHint.className="forge-intro";
    selectionHint.textContent="Choosing another element loads its base prompt.";selectionCard.append(selectionHint);
    catSel.disabled=true;elemSel.disabled=true;

    const prompt = document.createElement("textarea");
    prompt.placeholder = "pick an element above — its prompt appears here, ready to edit";
    prompt.spellcheck = false;
    prompt.ariaLabel="Generation prompt";
    const promptCard=document.createElement("section");promptCard.className="forge-card forge-prompt";
    const promptTitle=document.createElement("div");promptTitle.className="forge-card-title";promptTitle.textContent="02 / REFINE THE PROMPT";
    const promptFooter=document.createElement("div");promptFooter.className="forge-footer";
    const resetPrompt=document.createElement("button");resetPrompt.className="fcore-btn";resetPrompt.textContent="Restore base prompt";resetPrompt.disabled=true;
    resetPrompt.title="Replace your edited prompt with the selected element's base prompt.";
    const promptCount=document.createElement("span");promptCount.className="fcore-hint";
    const updateCount=()=>{promptCount.textContent=`${prompt.value.length} characters`;};
    promptFooter.append(resetPrompt,promptCount);promptCard.append(promptTitle,prompt,promptFooter);

    const styleRow = document.createElement("div");
    styleRow.className = "rowline";
    const styleToggle = document.createElement("input");
    styleToggle.type = "checkbox";
    const styleLab = document.createElement("label");
    styleLab.textContent = "Enable style";styleLab.className="forge-style-toggle";
    styleLab.style.cssText = "color:#aaa;font-size:11px;";
    // A shelf of real conditions -- a lens era, a stock, weather, what is
    // lighting it, what is on the matte box -- rather than one suggestion.
    // Picking one fills the box below, which stays editable.
    const styleSel = document.createElement("select");
    styleSel.className = "fcore-src-sel";
    styleSel.style.cssText = "flex:1 1 auto;min-width:0;";
    styleLab.prepend(styleToggle);styleSel.ariaLabel="Style starting point";
    styleRow.append(styleLab, styleSel);
    // its own multi-line box: a style tail is a sentence, not a word
    const styleText = document.createElement("textarea");
    styleText.className = "styletext";
    styleText.spellcheck = false;
    styleText.value = EXTRA_STYLE_SUGGESTION;
    styleText.ariaLabel="Additional style prompt";
    const styleCard=document.createElement("details");styleCard.className="forge-card";
    const styleSummary=document.createElement("summary");styleSummary.textContent="03 / Optional styling";
    const styleBody=document.createElement("div");styleBody.className="forge-style-body";
    styleBody.append(styleRow,styleText);styleCard.append(styleSummary,styleBody);

    const hint = document.createElement("div");
    hint.className = "fcore-hint";
    hint.setAttribute("role","status");hint.textContent="Loading element library…";
    root.append(header,selectionCard,promptCard,styleCard,hint);

    for (const el of [catSel, elemSel, prompt, styleToggle, styleSel, styleText]) {
      el.addEventListener("pointerdown", (e) => e.stopPropagation());
    }

    let bank = {};
    const syncStyle = () => {
      styleText.disabled = !styleToggle.checked;
      styleText.style.opacity = styleToggle.checked ? "1" : "0.45";
      setStr(node, "extra_style", styleToggle.checked ? styleText.value : "");
      hint.textContent = styleToggle.checked
        ? "style is appended to the prompt when you queue"
        : "queue renders the prompt exactly as written above";
    };
    const fillElems = (cat, keep) => {
      elemSel.textContent = "";
      for (const name of Object.keys(bank[cat] || {})) {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = name.replace(/_/g, " ");
        elemSel.appendChild(o);
      }
      if (keep && bank[cat] && keep in bank[cat]) elemSel.value = keep;
    };
    const applySelection = (loadPrompt) => {
      const cat = catSel.value, name = elemSel.value;
      if (!cat || !name) return;
      setStr(node, "element", `${cat}/${name}`);
      if (loadPrompt) {
        prompt.value = bank[cat]?.[name] || "";
        setStr(node, "custom_prompt", prompt.value);
      }
      updateCount();
      node.setDirtyCanvas(true, false);
    };

    catSel.onchange = () => { fillElems(catSel.value); applySelection(true); };
    elemSel.onchange = () => applySelection(true);
    resetPrompt.onclick=()=>applySelection(true);
    prompt.addEventListener("input", () => setStr(node, "custom_prompt", prompt.value));
    prompt.addEventListener("input",updateCount);
    styleToggle.onchange = syncStyle;
    styleText.addEventListener("input", syncStyle);

    let styles = {};
    const fillStyles = () => {
      styleSel.textContent = "";
      const custom = document.createElement("option");
      custom.value = "";
      custom.textContent = "custom…";
      styleSel.appendChild(custom);
      for (const [group, entries] of Object.entries(styles)) {
        const og = document.createElement("optgroup");
        og.label = group;
        for (const name of Object.keys(entries)) {
          const o = document.createElement("option");
          o.value = `${group}/${name}`;
          o.textContent = name;
          og.appendChild(o);
        }
        styleSel.appendChild(og);
      }
    };
    // typing in the box means the tail is no longer one of the presets
    styleText.addEventListener("input", () => { styleSel.value = ""; });
    styleSel.onchange = () => {
      const [group, name] = styleSel.value.split("/");
      const text = styles[group]?.[name];
      if (!text) return;
      styleText.value = text;
      styleToggle.checked = true;
      syncStyle();
    };

    api.fetchApi("/flarecore/prompt_bank").then((r) => r.json()).then((d) => {
      bank = d.bank || {};
      if (!Object.keys(bank).length) throw new Error("Empty prompt bank");
      catSel.disabled=false;elemSel.disabled=false;resetPrompt.disabled=false;
      styles = d.styles || {};
      fillStyles();
      catSel.textContent = "";
      for (const cat of Object.keys(bank)) {
        const o = document.createElement("option");
        o.value = cat;
        o.textContent = cat.replace(/_/g, " ");
        catSel.appendChild(o);
      }
      // restore what the workflow saved: the element combo names the pick,
      // custom_prompt holds the (possibly edited) text, extra_style the tail
      const saved = getStr(node, "element");
      const [cat, name] = saved.includes("/") ? saved.split("/") : [null, null];
      if (cat && bank[cat]) {
        catSel.value = cat;
        fillElems(cat, name);
      } else {
        fillElems(catSel.value);
      }
      const savedPrompt = getStr(node, "custom_prompt");
      const savedStyle = getStr(node, "extra_style");
      if (savedStyle) {
        styleCard.open=true;
        styleToggle.checked = true;
        styleText.value = savedStyle;
        // show which preset it came from, when it is still one of them
        for (const [group, entries] of Object.entries(styles)) {
          for (const [name, text] of Object.entries(entries)) {
            if (text === savedStyle) styleSel.value = `${group}/${name}`;
          }
        }
      }
      if (savedPrompt) prompt.value = savedPrompt;
      else applySelection(true);
      applySelection(false);
      syncStyle();
    }).catch(() => { hint.textContent = "could not load the prompt bank"; });

    // Fluid like the flare editor: the panel takes whatever node height is
    // left below it, and the prompt box (flex: 1 in the CSS) absorbs the
    // slack — drag the node taller and you get more prompt, not dead space.
    // The layout engine asks options.getMinHeight (via the widget's
    // computeLayoutSize) and ignores widget.computeSize entirely — measured:
    // computeSize reported 754 while computeLayoutSize still said 150, and
    // the panel stayed at its minimum. So the fluid height has to be
    // reported HERE. It is always node height minus the panel's own offset,
    // so it can never drive the node bigger than it already is.
    const widget = node.addDOMWidget("forge_panel", "flarecore.forge", root,
      { serialize: false, hideOnZoom: true,
        getMinHeight: () => node._fcForgeH ?? 260 });
    widget.serialize = false;
    widget.serializeValue = () => undefined;
    widget.computeSize = (w) =>
      [Number(w) || node.size?.[0] || 380, node._fcForgeH ?? 220];
    // only grow a node that was never sized by the workflow
    if (node.size[0] < 380 || node.size[1] < 260) {
      node.setSize([Math.max(node.size[0], 400), Math.max(node.size[1], 300)]);
    }
    setTimeout(() => fitForge(node), 40);
  };

  const onResize = nodeType.prototype.onResize;
  nodeType.prototype.onResize = function (size) {
    onResize?.apply(this, arguments);
    fitForge(this);
  };

  // reloads carry one null slot for the DOM panel; nothing else to repair,
  // but re-hide the raw widgets the frontend just rebuilt
  const onConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () {
    onConfigure?.apply(this, arguments);
    for (const w of this.widgets || []) {
      if (w.name !== "forge_panel") hideWidget(w);
    }
    setTimeout(() => { fitForge(this); this.setDirtyCanvas(true, true); }, 60);
  };
}

/* ---------------------------------------------------------- studio switch */

// The studio workflow carries all three benches; this virtual node is the
// switch between them. It mutes every node inside the two inactive groups
// (mode 2 = NEVER) so exactly one bench queues, and dims their group colour
// so the graph reads at a glance. Frontend-only: it never reaches the API.
const STUDIO_SECTIONS = [
  ["element forge", "ELEMENT FORGE"],
  ["flare lab", "FLARE LAB"],
  ["video lab", "VIDEO LAB"],
];
const GROUP_ACTIVE = { "ELEMENT FORGE": "#59453f", "FLARE LAB": "#3f5159",
                       "VIDEO LAB": "#3f4459" };
const GROUP_DIM = "#26262b";

// Membership is computed here rather than through group.recomputeInsideNodes()
// on purpose: that walks each node's cached bounding box, which is only filled
// in once the canvas has drawn. On a freshly loaded workflow it reports an
// empty group, so the benches all came up live and the switch looked dead
// until you clicked something. A node's centre against the group rectangle
// needs no cache and gives the same answer.
function nodesInGroup(graph, group) {
  const b = group._bounding || group.bounding || [0, 0, 0, 0];
  const [gx, gy, gw, gh] = b;
  return (graph._nodes || []).filter((n) => {
    const cx = n.pos[0] + (n.size?.[0] || 0) / 2;
    const cy = n.pos[1] + (n.size?.[1] || 0) / 2;
    return cx >= gx && cx <= gx + gw && cy >= gy && cy <= gy + gh;
  });
}

// Converting a selection into a subgraph copies each node's CURRENT mode into
// the definition, permanently: muting reaches the subgraph INSTANCE in the
// parent graph, never the nodes inside it. Build a subgraph while its bench is
// muted -- the normal way to build one here -- and the definition is frozen
// dead, so activating the bench gives you a generator that contributes nothing
// and a "Required input is missing" on the node downstream of it, far from the
// cause. Every single inner node disabled is never a deliberate setup (it is
// just an expensive way to mute the instance), so treat it as the trap it is.
// SOME nodes bypassed is a real choice and is left alone.
function reviveDeadSubgraph(node) {
  const inner = node.subgraph?.nodes;
  if (!inner || !inner.length) return;
  if (inner.some((n) => (n.mode ?? 0) === 0)) return;
  for (const n of inner) n.mode = 0;
}

/* ------------------------------------------------- generator switch --- */

// Everything that feeds one input of a switch, found by walking the links
// rather than by group titles, so the switch works in any graph.
function upstreamOf(node, inputName) {
  const seen = new Set();
  const idx = (node.inputs || []).findIndex((i) => i.name === inputName);
  if (idx < 0) return seen;
  const stack = [node.getInputNode(idx)];
  while (stack.length) {
    const n = stack.pop();
    if (!n || seen.has(n)) continue;
    seen.add(n);
    for (let s = 0; s < (n.inputs?.length || 0); s++) {
      const up = n.getInputNode(s);
      if (up) stack.push(up);
    }
  }
  return seen;
}

// The lazy input already guarantees the unselected branch never RUNS; this
// makes it also LOOK disabled. Nodes feeding both branches (the prompt
// node, usually) are shared plumbing and stay untouched.
function applyGeneratorChoice(node) {
  if (!node.graph) return;
  const choice = String(findWidget(node, "generator")?.value ?? "a");
  const a = upstreamOf(node, "image_a");
  const b = upstreamOf(node, "image_b");
  const [active, inactive] = choice === "a" ? [a, b] : [b, a];
  for (const n of inactive) {
    if (!active.has(n)) n.mode = 2;        // 2 = NEVER (muted)
  }
  for (const n of active) {
    if (!inactive.has(n) && n.mode === 2) {
      n.mode = 0;
      reviveDeadSubgraph(n);
    }
  }
  node.graph.setDirtyCanvas?.(true, true);
}

function reapplyGeneratorChoices(graph) {
  for (const n of graph?._nodes || []) {
    if (n.type === "FlareGeneratorSelect") applyGeneratorChoice(n);
  }
}

function applyStudioSection(graph, active) {
  for (const g of graph._groups || []) {
    const entry = STUDIO_SECTIONS.find(([, t]) =>
      (g.title || "").toUpperCase().startsWith(t));
    if (!entry) continue;
    const on = entry[0] === active;
    for (const n of nodesInGroup(graph, g)) {
      if (n.type === "FlarecoreStudioSwitch") continue;
      n.mode = on ? 0 : 2;                 // 2 = NEVER (muted)
      if (on) reviveDeadSubgraph(n);
    }
    g.color = on ? (GROUP_ACTIVE[entry[1]] || g.color) : GROUP_DIM;
  }
  // waking a bench force-unmutes everything in it, which would re-enable
  // the generator branch the switch has parked; let the switch re-decide
  reapplyGeneratorChoices(graph);
  graph.setDirtyCanvas?.(true, true);
}

function registerStudioSwitch(app) {
  // The current frontend expects a real LGraphNode subclass; a plain class
  // no longer gets the prototype grafted on and half the node API is missing.
  class FlarecoreStudio extends LiteGraph.LGraphNode {
    constructor() {
      super("Flarecore Studio");
      this.isVirtualNode = true;           // stays out of the API prompt
      this.serialize_widgets = false;      // the choice lives in properties
      this.size = [250, 120];
      this.properties = { active: "flare lab" };
      // NATIVE widgets, deliberately not a DOM panel: this is a global
      // control that has to work while zoomed out far enough to see all
      // three benches, and ComfyUI hides DOM widgets below ~50% zoom.
      // litegraph draws these on the canvas at any scale.
      for (const [key] of STUDIO_SECTIONS) {
        this.addWidget("button", key, null, () => this.setActive(key));
      }
    }

    // A copy of this node can sit above each bench, so they all have to
    // agree: whichever one is clicked, every switch in the graph re-marks
    // itself. Only the clicked one touches the graph.
    relabel(key) {
      this.properties.active = key;
      STUDIO_SECTIONS.forEach(([k], i) => {
        const w = this.widgets?.[i];
        if (!w) return;
        const title = k.replace(/^./, (c) => c.toUpperCase());
        w.name = (k === key ? "\u25cf  " : "\u25cb  ") + title;
        w.label = w.name;
      });
      this.setDirtyCanvas?.(true, true);
    }

    setActive(key) {
      this.relabel(key);
      const graph = this.graph;
      if (!graph) return;
      for (const n of graph._nodes || []) {
        if (n !== this && n.type === "FlarecoreStudioSwitch") n.relabel?.(key);
      }
      applyStudioSection(graph, key);
    }

    onAdded() { setTimeout(() => this.setActive(this.properties.active), 30); }
    onConfigure() { setTimeout(() => this.setActive(this.properties.active), 60); }
  }
  FlarecoreStudio.title = "Flarecore Studio";
  FlarecoreStudio.category = "flare";
  LiteGraph.registerNodeType("FlarecoreStudioSwitch", FlarecoreStudio);
}

/* -------------------------------------------------------------- extension */

app.registerExtension({
  name: "flarecore.ui",
  registerCustomNodes(app) {
    registerStudioSwitch(app);
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "FlareGeneratorSelect") {
      const onNodeCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        onNodeCreated?.apply(this, arguments);
        const w = findWidget(this, "generator");
        if (w) {
          const prior = w.callback;
          w.callback = (...args) => {
            prior?.(...args);
            applyGeneratorChoice(this);
          };
        }
      };
      // links only exist after the whole graph is configured, so the
      // first application is deferred a beat
      const onConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function () {
        onConfigure?.apply(this, arguments);
        setTimeout(() => applyGeneratorChoice(this), 60);
      };
      const onConnectionsChange = nodeType.prototype.onConnectionsChange;
      nodeType.prototype.onConnectionsChange = function () {
        onConnectionsChange?.apply(this, arguments);
        if (this.graph) setTimeout(() => applyGeneratorChoice(this), 0);
      };
      return;
    }
    if (nodeData.name === "FlareElementPrompts") {
      setupForgePanel(nodeType);
      return;
    }
    if (nodeData.name !== "FlareRender") return;
    injectCSS();

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onNodeCreated?.apply(this, arguments);
      const node = this;

      const picker = new PointPicker(node);
      node._fcPicker = picker;
      const pickerWidget = node.addDOMWidget("flare_layout", "flarecore.layout",
        picker.el, { serialize: false, hideOnZoom: true, getMinHeight: () => 170 });
      pickerWidget.serialize = false;
      pickerWidget.serializeValue = () => undefined;
      // the frontend sometimes calls computeSize() with no argument
      pickerWidget.computeSize = (w) => {
        const width = Number(w) || node.size?.[0] || 460;
        return [width, Math.min((width * 9) / 16 + 12, 330)];
      };

      const editor = new FlareEditor(node);
      node._fcEditor = editor;
      // The editor's height follows the node: fitEditor() measures the
      // widget's y-offset after each layout and hands the remaining node
      // height to the editor, so dragging the node bigger gives more room
      // for sliders and elements instead of dead space + scrolling.
      const editorWidget = node.addDOMWidget("flare_editor", "flarecore.editor",
        editor.root, { serialize: false, hideOnZoom: true,
          getMinHeight: () => Math.max(280, node._fcEditorH ?? 400) });
      editorWidget.serialize = false;
      editorWidget.serializeValue = () => undefined;
      editorWidget.computeSize = (w) =>
        [Number(w) || node.size?.[0] || 500, node._fcEditorH ?? 400];

      // The panels stay at the END of node.widgets: widgets_values is a
      // positional array and ComfyUI serializes a null for each DOM widget,
      // so any other position shifts every real value on reload.
      setAdvanced(node, false);
      node.setSize([Math.max(node.size[0], 500),
                    Math.max(node.computeSize()[1], 880)]);
      setTimeout(() => picker.draw(), 60);
    };

    // Manual node resize: hand the new leftover height to the editor.
    const onResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function (size) {
      onResize?.apply(this, arguments);
      fitEditor(this);
    };

    // Tear the panels down with the node: the picker holds an interval, a
    // ResizeObserver and pointer listeners; the editor holds an interval.
    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this._fcPicker?.destroy();
      this._fcEditor?.destroy();
      onRemoved?.apply(this, arguments);
    };

    // Restore the compact layout for nodes loaded from a saved workflow, and
    // repair values saved by the build that placed the panels first.
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      onConfigure?.apply(this, arguments);
      const node = this;

      // A workflow written by the one build that placed the panels first
      // starts with exactly one null per panel widget; a correct save ends
      // with them (or trims them). The guard requires BOTH the null prefix
      // and the exact length that build produced, so a future graph that
      // legitimately serializes a leading null cannot trip it.
      const vals = info?.widgets_values;
      const real = node.widgets.filter((w) => !PANEL_WIDGETS.has(w.name));
      if (Array.isArray(vals) && vals.length === real.length + 2 &&
          vals[0] === null && vals[1] === null) {
        const shifted = vals.slice(2);
        real.forEach((w, i) => {
          if (shifted[i] !== null && shifted[i] !== undefined) {
            w.value = shifted[i];
          }
        });
        console.log("[flarecore] repaired widget values from a shifted save");
      }

      const savedHeight = Array.isArray(info?.size) ? info.size[1] : null;
      setTimeout(() => {
        // the toggle state rides in node.properties, which IS serialized
        setAdvanced(node, !!node.properties?.fc_advanced);
        // setAdvanced only grows; if the user saved the node taller, keep it
        if (savedHeight && savedHeight > node.size[1]) {
          node.setSize([node.size[0], savedHeight]);
        }
        node._fcEditor?.build();
        node._fcPicker?.draw();
      }, 50);
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      // fc_preview is the clean input plate for the picker backdrop; it is
      // NOT sent as ui.images so ComfyUI does not also paint a preview
      // image under the node
      const src = message?.fc_light_src?.[0];
      if(message?.fc_groups?.[0])this._fcGroupResults=message.fc_groups[0];
      const sourceStatus = message?.fc_source_status?.[0];
      if (sourceStatus) {
        this._fcSourceStatus=sourceStatus;
        this._fcEditor?.build();
      }
      if (src) {
        this._fcLightSrc = src;
        this._fcPicker?.draw();
      }
      // the path the light took this render: drawn on the picker, and the
      // source of "bake to path"
      const trk = message?.fc_track?.[0];
      if (Array.isArray(trk)) {
        this._fcTrack = trk;
        this._fcPicker?.draw();
        if (this._fcEditor && !this._fcEditor.root.querySelector(".fcore-bake")) {
          this._fcEditor.build();
        }
      }
      const imgs = message?.fc_preview ?? message?.images;
      if (imgs?.length && this._fcPicker) {
        const im = imgs[0];
        const url = api.apiURL(
          `/view?filename=${encodeURIComponent(im.filename)}` +
          `&type=${im.type}&subfolder=${encodeURIComponent(im.subfolder || "")}` +
          `&t=${Date.now()}`
        );
        const img = new Image();
        img.onload = () => this._fcPicker.setBackdrop(img);
        img.src = url;
      }
    };
  },
});
