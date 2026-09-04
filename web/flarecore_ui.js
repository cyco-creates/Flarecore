// SPDX-License-Identifier: Apache-2.0
// flarecore: the FlareRender interface — a point picker for the light and
// flare anchor, and a stack editor over preset_json with an element gallery.
//
// The editor IS the interface: every node widget stays hidden until the ⚙
// button reveals them. Both panels live in one extension so a single owner
// controls widget order and node height.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* ------------------------------------------------------------------ utils */

// The two DOM panels. They must stay at the end of node.widgets: ComfyUI
// writes a null into widgets_values for each of them instead of omitting
// them, so any other position shifts every real value on reload.
const PANEL_WIDGETS = new Set(["flare_layout", "flare_editor"]);

// Everything else on the node hides behind the ⚙ button — the picker and
// the editor are the interface; the widgets are the escape hatch.
const NODE_WIDGETS = [
  "preset_json", "position_mode",
  "light_x", "light_y", "flare_x", "flare_y",
  "detect_threshold", "detect_max_lights",
  "occlusion_radius", "light_depth", "invert_depth",
  "intensity", "scale", "blend_mode", "clamp_output",
  "seed", "control_after_generate", "occlusion_smooth",
];

function hideWidget(w) {
  if (w._fcHidden) return;
  w._fcHidden = true;
  w._fcType = w.type;
  w._fcCompute = w.computeSize;
  w.type = "hidden";
  w.computeSize = () => [0, -4];
  if (w.element?.style) w.element.style.display = "none";
}

function showWidget(w) {
  if (!w._fcHidden) return;
  w._fcHidden = false;
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
  for (const name of NODE_WIDGETS) {
    const w = node.widgets?.find((x) => x.name === name);
    if (w) (visible ? showWidget : hideWidget)(w);
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
  const w = findWidget(node, name);
  return w ? Number(w.value) : fallback;
}
function setVal(node, name, value) {
  const w = findWidget(node, name);
  if (w) {
    w.value = Math.round(value * 1000) / 1000;
    w.callback?.(w.value, app.canvas, node, null, null);
  }
}

function elementThumbUrl(ref) {
  return api.apiURL(`/flarecore/element/${ref.split("/").map(encodeURIComponent).join("/")}`);
}

/* ----------------------------------------------------------- point picker */

class PointPicker {
  constructor(node) {
    this.node = node;
    this.backdrop = null;
    this.drag = null;

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

  imageRect() {
    const [W, H] = this.cssSize;
    const aspect = this.backdrop
      ? this.backdrop.width / this.backdrop.height : 16 / 9;
    let w = W, h = w / aspect;
    if (h > H) { h = H; w = h * aspect; }
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
    if (this.backdrop) {
      ctx.drawImage(this.backdrop, r.x, r.y, r.w, r.h);
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

    // light handle: a plain ring and dot — no sun-ray decoration, which
    // read as a rendered sun on the backdrop
    ctx.strokeStyle = "#ffb648";
    ctx.fillStyle = "rgba(255,182,72,0.18)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(lx, ly, 9, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#ffb648";
    ctx.beginPath(); ctx.arc(lx, ly, 2.5, 0, Math.PI * 2); ctx.fill();

    ctx.strokeStyle = "#5fd7ff";
    ctx.fillStyle = "rgba(95,215,255,0.18)";
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

    if (e.type === "pointerdown") {
      const [lx, ly] = this.point("light_x", "light_y", r);
      const [fx, fy] = this.point("flare_x", "flare_y", r);
      const dl = Math.hypot(x - lx, y - ly);
      const df = Math.hypot(x - fx, y - fy);
      this.drag = dl <= df ? "light" : "flare";
      this.canvas.setPointerCapture(e.pointerId);
    } else if (e.type === "pointermove" && this.drag) {
      const u = Math.min(1, Math.max(0, (x - r.x) / r.w));
      const v = Math.min(1, Math.max(0, (y - r.y) / r.h));
      setVal(this.node, this.drag === "light" ? "light_x" : "flare_x", u);
      setVal(this.node, this.drag === "light" ? "light_y" : "flare_y", v);
      this.node.setDirtyCanvas(true, false);
      this.draw();
    } else if (e.type === "pointerup" || e.type === "pointercancel") {
      if (!this.drag) return;
      this.drag = null;
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
  ["Fog", "fog"], ["Disc", "disc"],
  ["Iris ghost", "iris"], ["Multi-iris", "multi_iris"],
  ["Spike ball", "spike_ball"], ["Shimmer", "shimmer"],
  ["Sparkle", "sparkle"], ["Rays", "rays"],
  ["Streak", "streak"], ["Stripe", "stripe"],
  ["Ring", "ring"], ["Hoop", "hoop"], ["Spectral", "spectral"],
  ["From library…", "texture"],
];

const ADD_DEFAULTS = {
  glow: { type: "glow", label: "glow", offset: 0, scale: 0.4, intensity: 1, color: [1, 0.95, 0.85], params: { softness: 0.35, falloff: 1.3 } },
  bloom: { type: "glow", label: "bloom", offset: 0, scale: 2.2, intensity: 0.5, auto_rotate: false, light_mask: 1, color: [1, 0.97, 0.9], params: { softness: 1.1, falloff: 0.7 } },
  lens_dirt: { type: "texture", label: "lens dirt", offset: 0, scale: 1.3, intensity: 0.7, auto_rotate: false, screen_space: true, light_mask: 1, params: { file: "", channel: "auto" } },
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
  spectral: { type: "spectral", label: "spectral", offset: 1.7, scale: 0.45, intensity: 0.15, params: { shape: "ring", radius: 1, thickness: 0.06 } },
  texture: { type: "texture", label: "element", offset: 0.6, scale: 0.3, intensity: 0.6, params: { file: "", channel: "auto" } },
};

const COMMON_SPECS = {
  irregular: [0, 1, 0.01],
  light_mask: [0, 1, 0.01],
  dispersion: [0, 3, 0.05], dispersion_samples: [3, 15, 2],
  rotation: [-180, 180, 1], count: [1, 24, 1], spread: [0, 1, 0.01],
  count_falloff: [0.1, 1, 0.01], count_scale_step: [0.5, 2, 0.01],
};

// What an ABSENT key means (the schema's defaults) — without these an unset
// slider would display its range minimum, e.g. rotation reading -180.
const COMMON_DEFAULTS = {
  irregular: 0,
  light_mask: 0,
  dispersion: 0, dispersion_samples: 3, rotation: 0, count: 1, spread: 0,
  count_falloff: 1, count_scale_step: 1,
};

const PARAM_FALLBACKS = {
  glow: { softness: 0.35, falloff: 1.2 },
  iris: { blades: 6, edge_softness: 0.15, hollow: 0 },
  streak: { length: 0.8, thickness: 0.02, count: 1 },
  ring: { radius: 0.5, thickness: 0.05 },
  hoop: { radius: 0.6, thickness: 0.15, angular_falloff: 0.8 },
  glint: { points: 8, length: 0.5, thickness: 0.008, length_jitter: 0.3 },
  spectral: { radius: 0.5, thickness: 0.08, blades: 8, edge_softness: 0.1, hollow: 0 },
  texture: {},
};

const PARAM_SPECS = {
  glow: { softness: [0.01, 2, 0.01], falloff: [0.05, 6, 0.05] },
  iris: { blades: [3, 24, 1], edge_softness: [0, 1, 0.01], hollow: [0, 0.95, 0.01] },
  streak: { length: [0.01, 4, 0.01], thickness: [0.001, 0.5, 0.001], count: [1, 8, 1] },
  ring: { radius: [0, 2, 0.01], thickness: [0.001, 0.5, 0.001] },
  hoop: { radius: [0, 2, 0.01], thickness: [0.001, 1, 0.001], angular_falloff: [0, 1, 0.01] },
  glint: { points: [2, 256, 1], length: [0.01, 3, 0.01], thickness: [0.001, 0.1, 0.001], length_jitter: [0, 1, 0.01] },
  spectral: { radius: [0, 2, 0.01], thickness: [0.001, 0.5, 0.001], blades: [3, 24, 1], edge_softness: [0, 1, 0.01], hollow: [0, 0.95, 0.01] },
  texture: {},
};

const CSS = `
.fcore { font: 12px/1.4 sans-serif; color: #ccc; background: #131317;
  border: 1px solid #2b2b33; border-radius: 10px; padding: 8px;
  display: flex; flex-direction: column; gap: 7px; box-sizing: border-box;
  height: 100%; overflow: hidden; }
.fcore * { box-sizing: border-box; }
.fcore-bar { display: flex; gap: 6px; align-items: center; }
.fcore-btn { background: #1e1e25; color: #ddd; border: 1px solid #34343e;
  border-radius: 7px; padding: 5px 12px; cursor: pointer; font-size: 12px; }
.fcore-btn:hover { background: #2a2a33; border-color: #e8a33d; }
.fcore-btn.accent { color: #e8a33d; }
.fcore-btn.on { border-color: #e8a33d; color: #e8a33d; }
.fcore-badge { color: #ff7676; margin-left: auto; font-size: 11px; }
.fcore-list { overflow-y: auto; display: flex; flex-direction: column;
  gap: 5px; flex: 1; min-height: 60px; }
.fcore-row { background: #1a1a20; border: 1px solid #2b2b33;
  border-radius: 9px; padding: 7px 9px; }
.fcore-row.off { opacity: 0.4; }
.fcore-head { display: flex; align-items: center; gap: 8px; }
.fcore-chip { width: 30px; height: 30px; border-radius: 7px; flex: 0 0 30px;
  background: #101014; border: 1px solid #2b2b33; overflow: hidden;
  display: flex; align-items: center; justify-content: center; }
.fcore-chip img { width: 100%; height: 100%; object-fit: cover; }
.fcore-chip .ico { width: 16px; height: 16px; display: block; }
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
.fcore-col input[type=range] { width: 100%; height: 12px; accent-color: #e8a33d; }
.fcore-col input[type=number] { width: 100%; background: #101014; color: #ddd;
  border: 1px solid #2b2b33; border-radius: 5px; font-size: 11px;
  padding: 2px 4px; text-align: center; -moz-appearance: textfield; }
.fcore-col input[type=number]::-webkit-inner-spin-button { display: none; }
.fcore-swatch { width: 24px; height: 24px; padding: 0; border: 1px solid #34343e;
  border-radius: 6px; background: none; cursor: pointer; flex: 0 0 24px; }
.fcore-acts { display: flex; gap: 3px; }
.fcore-mini { background: #1e1e25; border: 1px solid #2b2b33; color: #999;
  cursor: pointer; border-radius: 6px; width: 26px; height: 26px;
  font-size: 12px; display: flex; align-items: center; justify-content: center; }
.fcore-mini:hover { color: #fff; border-color: #e8a33d; }
.fcore-adv { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 5px 10px;
  padding: 8px 2px 2px 40px; border-top: 1px dashed #2b2b33; margin-top: 7px; }
.fcore-adv select { background: #1e1e25; color: #ddd; border: 1px solid #34343e;
  border-radius: 5px; font-size: 11px; width: 100%; height: 22px; }
.fcore-global { display: flex; gap: 10px; align-items: center;
  background: #1a1a20; border: 1px solid #2b2b33; border-radius: 9px;
  padding: 7px 10px; }
.fcore-global label { color: #aaa; font-size: 12px; }
.fcore-global input[type=range] { flex: 1; accent-color: #e8a33d; height: 12px; }
.fcore-global input[type=number] { width: 56px; background: #101014;
  color: #ddd; border: 1px solid #2b2b33; border-radius: 5px;
  padding: 3px 4px; text-align: center; font-size: 11px; }
.fcore-menu { position: fixed; z-index: 10000; background: #1a1a20;
  border: 1px solid #34343e; border-radius: 8px; padding: 4px;
  display: flex; flex-direction: column; min-width: 160px; max-height: 60vh;
  overflow-y: auto; box-shadow: 0 6px 24px rgba(0,0,0,0.55); }
.fcore-menu button { background: none; border: none; color: #ccc;
  text-align: left; padding: 6px 10px; cursor: pointer; font-size: 12px;
  border-radius: 5px; }
.fcore-menu button:hover { background: #2a2a33; color: #fff; }
.fcore-shade { position: fixed; inset: 0; z-index: 10001;
  background: rgba(0,0,0,0.6); display: flex; align-items: center;
  justify-content: center; }
.fcore-gal { background: #17171c; border: 1px solid #34343e;
  border-radius: 12px; width: min(760px, 92vw); max-height: 82vh;
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
.fcore-thumb { background: #000; border: 1px solid #2b2b33; border-radius: 8px;
  overflow: hidden; cursor: pointer; text-align: center; }
.fcore-thumb:hover { border-color: #e8a33d; }
.fcore-thumb.sel { border-color: #5fd7ff; box-shadow: 0 0 0 1px #5fd7ff; }
.fcore-thumb img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
.fcore-thumb span { display: block; padding: 3px 4px; font-size: 10px;
  color: #999; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fcore-gal-empty { color: #777; text-align: center; padding: 30px 10px; }
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
function sliderCol(label, value, [min, max, step], onChange) {
  const wrap = document.createElement("div");
  wrap.className = "fcore-col";
  const lab = document.createElement("label");
  lab.textContent = label;
  const range = document.createElement("input");
  range.type = "range"; range.min = min; range.max = max; range.step = step;
  const real = Number.isFinite(Number(value)) ? Number(value) : min;
  range.value = Math.min(max, Math.max(min, real));
  const num = document.createElement("input");
  num.type = "number"; num.min = min; num.step = step;
  const fmt = (v) => Number(v).toFixed(step >= 1 ? 0 : 2);
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
  }

  // Remap the expanded-row set across a structural change so the twirled-
  // open panel stays with ITS element instead of whatever lands on its index.
  remapExpanded(fn) {
    this.expanded = new Set([...this.expanded].map(fn).filter((i) => i >= 0));
  }

  get widget() { return findWidget(this.node, "preset_json"); }

  read() {
    try {
      const preset = JSON.parse(this.widget?.value || "{}");
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

  write(preset) {
    const text = JSON.stringify(preset, null, 2);
    if (this.widget) { this.widget.value = text; this.lastText = text; }
    this.node.setDirtyCanvas(true, false);
  }

  mutate(fn) {
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
      requestAnimationFrame(() => {
        if (this._pending) {
          this.write(this._pending);
          this._pending = null;
        }
      });
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
      openGallery(this.libraryFiles, {
        title: empty
          ? `no ${cat.replace(/_/g, " ")} elements yet — showing everything`
          : cat ? `${cat.replace(/_/g, " ")} — pick one` : "All elements",
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
      }));
    });
  }

  build() {
    this.root.textContent = "";
    const preset = this.read();

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
    loadBtn.className = "fcore-btn";
    loadBtn.textContent = "presets ▾";
    loadBtn.onclick = async (e) => {
      try {
        const r = await api.fetchApi("/flarecore/presets");
        const d = await r.json();
        popupMenu(e, (d.presets || []).map((n) => [n.replace(/\.json$/, ""), n]),
          async (name) => {
            try {
              const rr = await api.fetchApi(
                `/flarecore/preset/${encodeURIComponent(name.replace(/\.json$/, ""))}`);
              const dd = await rr.json();
              if (dd.json && this.widget) {
                this.widget.value = dd.json;
                this.lastText = dd.json;
                this.build();
              } else if (dd.error) {
                loadBtn.textContent = "load failed";
                setTimeout(() => { loadBtn.textContent = "presets ▾"; }, 1800);
              }
            } catch (err) { console.error("flarecore preset load", err); }
          });
      } catch (err) { console.error("flarecore presets", err); }
    };

    const saveBtn = document.createElement("button");
    saveBtn.className = "fcore-btn";
    saveBtn.textContent = "save…";
    saveBtn.onclick = async () => {
      const name = prompt("Preset name:", "my_flare");
      if (!name) return;
      const post = (overwrite) => api.fetchApi("/flarecore/save_preset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, json: this.widget?.value || "", overwrite }),
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

    bar.append(addBtn, loadBtn, saveBtn, libBtn, advBtn);
    if (this.error) {
      const badge = document.createElement("span");
      badge.className = "fcore-badge";
      badge.textContent = "⚠ invalid JSON";
      bar.appendChild(badge);
    }
    this.root.appendChild(bar);
    if (!preset) return;

    /* global row: master slider + value + tint swatch */
    const g = preset.global;
    const gRow = document.createElement("div");
    gRow.className = "fcore-global";
    const gLab = document.createElement("label");
    gLab.textContent = "master";
    const gRange = document.createElement("input");
    gRange.type = "range"; gRange.min = 0; gRange.max = 3; gRange.step = 0.01;
    gRange.value = g.intensity ?? 1;
    const gNum = document.createElement("input");
    gNum.type = "number"; gNum.min = 0; gNum.max = 3; gNum.step = 0.01;
    gNum.value = Number(gRange.value).toFixed(2);
    gRange.addEventListener("input", () => {
      gNum.value = Number(gRange.value).toFixed(2);
      this.mutateQuiet((p) => { p.global.intensity = Number(gRange.value); });
    });
    gNum.addEventListener("change", () => {
      const v = Math.min(3, Math.max(0, Number(gNum.value) || 0));
      gNum.value = v.toFixed(2); gRange.value = v;
      this.mutateQuiet((p) => { p.global.intensity = v; });
    });
    const aLab = document.createElement("label");
    aLab.textContent = "aspect";
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
    gRow.append(gLab, gRange, gNum, aLab, aRange, aNum, tint);
    this.root.appendChild(gRow);

    /* element rows */
    const list = document.createElement("div");
    list.className = "fcore-list";
    preset.elements.forEach((elem, i) => list.appendChild(this.buildRow(elem, i)));
    if (!preset.elements.length) {
      const empty = document.createElement("div");
      empty.style.cssText = "color:#777;text-align:center;padding:16px;";
      empty.textContent = "no elements — + add, or pick from the library";
      list.appendChild(empty);
    }
    this.root.appendChild(list);
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
    return chip;
  }

  buildRow(elem, i) {
    const row = document.createElement("div");
    row.className = "fcore-row" + (elem.enabled === false ? " off" : "");

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

    const dropdown = (label, value, options, onPick) => {
      const wrap = document.createElement("div");
      wrap.className = "fcore-col";
      const l = document.createElement("label");
      l.textContent = label;
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
      adv.appendChild(dropdown("channel (luminance = full recolor)",
        elem.params?.channel || "auto",
        ["auto", "rgb", "luminance"], (v) => setParam("channel", v)));
    }

    const checkbox = (label, checked, onChange) => {
      const wrap = document.createElement("div");
      wrap.className = "fcore-col";
      const l = document.createElement("label");
      l.textContent = label;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.addEventListener("pointerdown", (e) => e.stopPropagation());
      cb.onchange = () => onChange(cb.checked);
      wrap.append(l, cb);
      return wrap;
    };
    adv.appendChild(checkbox("auto-rotate", elem.auto_rotate !== false,
      (v) => set("auto_rotate", v)));
    adv.appendChild(checkbox("screen space (lens)", elem.screen_space === true,
      (v) => set("screen_space", v)));

    for (const [key, spec] of Object.entries(COMMON_SPECS)) {
      adv.appendChild(sliderCol(key.replace(/_/g, " "),
        elem[key] ?? COMMON_DEFAULTS[key], spec,
        (v) => set(key, spec[2] >= 1 ? Math.round(v) : v)));
    }
    adv.appendChild(sliderCol("stretch x", elem.stretch?.[0] ?? 1, [0.1, 4, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].stretch || [1, 1];
        p.elements[i].stretch = [v, s[1]];
      })));
    adv.appendChild(sliderCol("stretch y", elem.stretch?.[1] ?? 1, [0.1, 4, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].stretch || [1, 1];
        p.elements[i].stretch = [s[0], v];
      })));

    if (elem.type === "spectral") {
      adv.appendChild(dropdown("shape", elem.params?.shape || "ring",
        ["ring", "iris"], (v) => setParam("shape", v)));
    }
    for (const [key, spec] of Object.entries(PARAM_SPECS[elem.type] || {})) {
      adv.appendChild(sliderCol(key.replace(/_/g, " "),
        elem.params?.[key] ?? PARAM_FALLBACKS[elem.type]?.[key], spec,
        (v) => setParam(key, spec[2] >= 1 ? Math.round(v) : v)));
    }
    return adv;
  }
}

/* -------------------------------------------------------------- extension */

app.registerExtension({
  name: "flarecore.ui",
  async beforeRegisterNodeDef(nodeType, nodeData) {
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
        editor.root, { serialize: false, hideOnZoom: true, getMinHeight: () => 280 });
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
