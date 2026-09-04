// SPDX-License-Identifier: Apache-2.0
// flarecore: the FlareRender interface — a point picker for the light and
// flare anchor, and a stack editor over preset_json.
//
// Both panels live in one extension so a single owner controls widget order
// and node height. The node would otherwise stack sixteen numeric widgets
// above the panels and stand ~1300px tall, which at any usable zoom shows
// the panels alone with no node chrome around them.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* ------------------------------------------------------------------ utils */

// The two DOM panels. They must stay at the end of node.widgets: ComfyUI
// writes a null into widgets_values for each of them instead of omitting
// them, so any other position shifts every real value on reload.
const PANEL_WIDGETS = new Set(["flare_layout", "flare_editor"]);

// Widgets the picker owns, plus the tuning that does not belong on the face
// of the node. Revealed by the editor's ⚙ button.
const ADVANCED = [
  "light_x", "light_y", "flare_x", "flare_y",
  "detect_threshold", "detect_max_lights",
  "occlusion_radius", "light_depth", "invert_depth",
  "clamp_output", "control_after_generate",
];

function hideWidget(w) {
  if (w._fcHidden) return;
  w._fcHidden = true;
  w._fcType = w.type;
  w._fcCompute = w.computeSize;
  w.type = "hidden";
  w.computeSize = () => [0, -4];
}

function showWidget(w) {
  if (!w._fcHidden) return;
  w._fcHidden = false;
  w.type = w._fcType;
  if (w._fcCompute) w.computeSize = w._fcCompute;
  else delete w.computeSize;
}

function setAdvanced(node, visible) {
  node._fcAdvanced = visible;
  // properties are serialized with the workflow, so the toggle survives
  // save/load — a plain JS field would silently reset to closed
  if (node.properties) node.properties.fc_advanced = visible;
  for (const name of ADVANCED) {
    const w = node.widgets?.find((x) => x.name === name);
    if (w) (visible ? showWidget : hideWidget)(w);
  }
  // grow when the widgets need more room, but never shrink a node the user
  // deliberately made taller
  const want = node.computeSize()[1];
  if (node.size[1] < want) node.setSize([node.size[0], want]);
  node.setDirtyCanvas(true, true);
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

/* ----------------------------------------------------------- point picker */

class PointPicker {
  constructor(node) {
    this.node = node;
    this.backdrop = null;
    this.drag = null;

    this.el = document.createElement("div");
    this.el.style.cssText =
      "width:100%;height:100%;background:#0d0d11;border:1px solid #303038;" +
      "border-radius:6px;overflow:hidden;box-sizing:border-box;";
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

    ctx.strokeStyle = "#ffb648";
    ctx.fillStyle = "rgba(255,182,72,0.25)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(lx, ly, 9, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(lx + Math.cos(a) * 11, ly + Math.sin(a) * 11);
      ctx.lineTo(lx + Math.cos(a) * 15, ly + Math.sin(a) * 15);
      ctx.stroke();
    }

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

  // Pointer coords in the same CSS-pixel space the drawing uses.
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
      // Anywhere in the frame grabs the nearer handle, so the points stay
      // reachable when they sit on top of each other.
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

const ICONS = {
  glow: "◉", iris: "⬡", streak: "─", ring: "○",
  hoop: "◠", glint: "✳", spectral: "◐", texture: "▦",
};

const ADD_MENU = [
  ["Glow", "glow"], ["Iris ghost", "iris"], ["Streak", "streak"],
  ["Ring", "ring"], ["Hoop", "hoop"], ["Glint", "glint"],
  ["Spectral", "spectral"], ["Texture (library)", "texture"],
];

const ADD_DEFAULTS = {
  glow: { type: "glow", offset: 0, scale: 0.4, intensity: 1, color: [1, 0.95, 0.85], params: { softness: 0.35, falloff: 1.3 } },
  iris: { type: "iris", offset: 0.7, scale: 0.12, intensity: 0.25, color: [0.85, 0.93, 1], dispersion: 0.4, params: { blades: 8, edge_softness: 0.3 } },
  streak: { type: "streak", offset: 0, scale: 1, intensity: 0.6, auto_rotate: false, color: [0.5, 0.7, 1], params: { length: 1.4, thickness: 0.012 } },
  ring: { type: "ring", offset: 1.4, scale: 0.4, intensity: 0.15, dispersion: 0.8, color: [1, 0.95, 1], params: { radius: 1, thickness: 0.08 } },
  hoop: { type: "hoop", offset: 0.5, scale: 0.8, intensity: 0.12, dispersion: 1, color: [1, 0.8, 0.6], params: { radius: 0.9, thickness: 0.22, angular_falloff: 0.8 } },
  glint: { type: "glint", offset: 0, scale: 0.7, intensity: 0.8, color: [1, 0.95, 0.85], params: { points: 12, length: 0.7, thickness: 0.007, length_jitter: 0.35 } },
  spectral: { type: "spectral", offset: 1.7, scale: 0.45, intensity: 0.15, params: { shape: "ring", radius: 1, thickness: 0.06 } },
  texture: { type: "texture", offset: 0.6, scale: 0.3, intensity: 0.6, params: { file: "", channel: "auto" } },
};

const COMMON_SPECS = {
  dispersion: [0, 3, 0.05], dispersion_samples: [3, 15, 2],
  rotation: [-180, 180, 1], count: [1, 24, 1], spread: [0, 1, 0.01],
  count_falloff: [0.1, 1, 0.01], count_scale_step: [0.5, 2, 0.01],
};

const PARAM_SPECS = {
  glow: { softness: [0.01, 2, 0.01], falloff: [0.05, 6, 0.05] },
  iris: { blades: [3, 24, 1], edge_softness: [0, 1, 0.01], hollow: [0, 0.95, 0.01] },
  streak: { length: [0.01, 4, 0.01], thickness: [0.001, 0.5, 0.001], count: [1, 8, 1] },
  ring: { radius: [0, 2, 0.01], thickness: [0.001, 0.5, 0.001] },
  hoop: { radius: [0, 2, 0.01], thickness: [0.001, 1, 0.001], angular_falloff: [0, 1, 0.01] },
  glint: { points: [2, 64, 1], length: [0.01, 3, 0.01], thickness: [0.001, 0.1, 0.001], length_jitter: [0, 1, 0.01] },
  spectral: { radius: [0, 2, 0.01], thickness: [0.001, 0.5, 0.001], blades: [3, 24, 1], edge_softness: [0, 1, 0.01], hollow: [0, 0.95, 0.01] },
  texture: {},
};

const CSS = `
.fcore { font: 11px/1.35 sans-serif; color: #ccc; background: #18181c;
  border: 1px solid #303038; border-radius: 6px; padding: 6px;
  display: flex; flex-direction: column; gap: 5px; box-sizing: border-box;
  height: 100%; overflow: hidden; }
.fcore * { box-sizing: border-box; }
.fcore-bar { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.fcore-btn { background: #26262e; color: #ddd; border: 1px solid #3a3a44;
  border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 11px; }
.fcore-btn:hover { background: #33333e; border-color: #e8a33d; }
.fcore-btn.accent { color: #e8a33d; }
.fcore-btn.on { border-color: #e8a33d; color: #e8a33d; }
.fcore-badge { color: #ff7676; margin-left: auto; font-size: 10px; }
.fcore-list { overflow-y: auto; display: flex; flex-direction: column;
  gap: 3px; flex: 1; min-height: 40px; }
.fcore-row { background: #202027; border: 1px solid #2e2e37; border-radius: 5px;
  padding: 3px 5px; }
.fcore-row.off { opacity: 0.45; }
.fcore-head { display: flex; align-items: center; gap: 5px; }
.fcore-ico { color: #e8a33d; width: 13px; text-align: center; }
.fcore-type { width: 52px; color: #eee; font-weight: 600; }
.fcore-sl { display: flex; align-items: center; gap: 3px; flex: 1; min-width: 70px; }
.fcore-sl label { color: #888; font-size: 9px; width: 24px; text-align: right; }
.fcore-sl input[type=range] { flex: 1; height: 10px; accent-color: #e8a33d;
  min-width: 30px; }
.fcore-sl output { width: 32px; font-size: 9px; color: #aaa; text-align: right; }
.fcore-mini { background: none; border: none; color: #888; cursor: pointer;
  padding: 0 3px; font-size: 11px; }
.fcore-mini:hover { color: #fff; }
.fcore-adv { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px;
  padding: 4px 2px 2px 20px; border-top: 1px dashed #2e2e37; margin-top: 3px; }
.fcore-adv .fcore-sl label { width: 74px; }
.fcore-adv select, .fcore-adv input[type=color] { background: #26262e;
  color: #ddd; border: 1px solid #3a3a44; border-radius: 3px; font-size: 10px;
  width: 100%; height: 18px; padding: 0 2px; }
.fcore-global { display: flex; gap: 8px; align-items: center;
  background: #202027; border: 1px solid #2e2e37; border-radius: 5px;
  padding: 3px 6px; }
.fcore-global .fcore-sl label { width: auto; }
.fcore-menu { position: fixed; z-index: 10000; background: #202027;
  border: 1px solid #3a3a44; border-radius: 5px; padding: 3px;
  display: flex; flex-direction: column; min-width: 150px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5); }
.fcore-menu button { background: none; border: none; color: #ccc;
  text-align: left; padding: 4px 8px; cursor: pointer; font-size: 11px;
  border-radius: 3px; }
.fcore-menu button:hover { background: #33333e; color: #fff; }
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
  menu.style.left = `${evt.clientX}px`;
  menu.style.top = `${evt.clientY}px`;
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

function slider(label, value, [min, max, step], onChange) {
  const wrap = document.createElement("div");
  wrap.className = "fcore-sl";
  const lab = document.createElement("label");
  lab.textContent = label;
  const inp = document.createElement("input");
  inp.type = "range"; inp.min = min; inp.max = max; inp.step = step;
  inp.value = value ?? min;
  const out = document.createElement("output");
  const show = (v) => { out.textContent = Number(v).toFixed(step >= 1 ? 0 : 2); };
  show(inp.value);
  inp.addEventListener("input", () => { show(inp.value); onChange(Number(inp.value)); });
  // keep drags inside the panel instead of panning the graph
  inp.addEventListener("pointerdown", (e) => e.stopPropagation());
  wrap.append(lab, inp, out);
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
      // texture dropdowns rendered before the fetch resolved showed
      // "<library empty>"; refresh them now the list exists
      if (this.expanded.size) this.build();
    } catch { this.libraryFiles = []; }
  }

  build() {
    this.root.textContent = "";
    const preset = this.read();

    const bar = document.createElement("div");
    bar.className = "fcore-bar";

    const addBtn = document.createElement("button");
    addBtn.className = "fcore-btn accent";
    addBtn.textContent = "+ add";
    addBtn.onclick = (e) => popupMenu(e, ADD_MENU, (type) => {
      const elem = JSON.parse(JSON.stringify(ADD_DEFAULTS[type]));
      // a stable id keeps this element's glint jitter its own, no matter how
      // the stack is later reordered
      elem.id = `${type}_${Date.now().toString(36)}${Math.floor(Math.random() * 46656).toString(36)}`;
      if (type === "texture" && this.libraryFiles.length) {
        elem.params.file = this.libraryFiles[0];
      }
      this.mutate((p) => p.elements.push(elem));
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

    const advBtn = document.createElement("button");
    advBtn.className = "fcore-btn" + (this.node._fcAdvanced ? " on" : "");
    advBtn.textContent = "⚙";
    advBtn.title = "show the node's advanced inputs (detection, occlusion, raw positions)";
    advBtn.onclick = () => { setAdvanced(this.node, !this.node._fcAdvanced); this.build(); };

    const renderBtn = document.createElement("button");
    renderBtn.className = "fcore-btn accent";
    renderBtn.textContent = "▶";
    renderBtn.title = "queue this workflow";
    renderBtn.onclick = () => app.queuePrompt(0);

    bar.append(addBtn, loadBtn, saveBtn, advBtn, renderBtn);
    if (this.error) {
      const badge = document.createElement("span");
      badge.className = "fcore-badge";
      badge.textContent = "⚠ invalid JSON";
      bar.appendChild(badge);
    }
    this.root.appendChild(bar);
    if (!preset) return;

    const g = preset.global;
    const gRow = document.createElement("div");
    gRow.className = "fcore-global";
    gRow.appendChild(slider("master", g.intensity ?? 1, [0, 3, 0.01],
      (v) => this.mutateQuiet((p) => { p.global.intensity = v; })));
    gRow.appendChild(slider("size", g.scale ?? 1, [0.1, 3, 0.01],
      (v) => this.mutateQuiet((p) => { p.global.scale = v; })));
    const tint = document.createElement("input");
    tint.type = "color";
    tint.title = "tint";
    tint.style.width = "26px";
    tint.value = colorToHex(g.tint || [1, 1, 1]);
    tint.addEventListener("pointerdown", (e) => e.stopPropagation());
    tint.addEventListener("input", () =>
      this.mutateQuiet((p) => { p.global.tint = hexToColor(tint.value); }));
    gRow.appendChild(tint);
    this.root.appendChild(gRow);

    const list = document.createElement("div");
    list.className = "fcore-list";
    preset.elements.forEach((elem, i) => list.appendChild(this.buildRow(elem, i)));
    if (!preset.elements.length) {
      const empty = document.createElement("div");
      empty.style.cssText = "color:#777;text-align:center;padding:12px;";
      empty.textContent = "no elements — add one above";
      list.appendChild(empty);
    }
    this.root.appendChild(list);
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

    const ico = document.createElement("span");
    ico.className = "fcore-ico";
    ico.textContent = ICONS[elem.type] || "?";
    const ty = document.createElement("span");
    ty.className = "fcore-type";
    ty.textContent = elem.type;
    ty.title = elem.type === "texture" ? (elem.params?.file || "no file") : elem.type;

    head.append(en, ico, ty);
    head.appendChild(slider("pos", elem.offset ?? 0, [-1, 3, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].offset = v; })));
    head.appendChild(slider("size", elem.scale ?? 0.5, [0.01, 2.5, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].scale = v; })));
    head.appendChild(slider("opac", elem.intensity ?? 1, [0, 3, 0.01],
      (v) => this.mutateQuiet((p) => { p.elements[i].intensity = v; })));

    const mk = (txt, title, fn) => {
      const b = document.createElement("button");
      b.className = "fcore-mini";
      b.textContent = txt;
      b.title = title;
      b.onclick = fn;
      return b;
    };
    head.appendChild(mk(this.expanded.has(i) ? "▴" : "▾", "more settings", () => {
      this.expanded.has(i) ? this.expanded.delete(i) : this.expanded.add(i);
      this.build();
    }));
    head.appendChild(mk("⧉", "duplicate", () => {
      this.remapExpanded((e) => (e > i ? e + 1 : e));
      this.mutate((p) => {
        const copy = JSON.parse(JSON.stringify(p.elements[i]));
        copy.id = ""; // the duplicate gets its own random identity
        p.elements.splice(i + 1, 0, copy);
      });
    }));
    head.appendChild(mk("↑", "move up", () => { if (i > 0) {
      this.remapExpanded((e) => (e === i ? i - 1 : e === i - 1 ? i : e));
      this.mutate((p) => p.elements.splice(i - 1, 0, p.elements.splice(i, 1)[0]));
    } }));
    head.appendChild(mk("↓", "move down", () => {
      this.remapExpanded((e) => (e === i ? i + 1 : e === i + 1 ? i : e));
      this.mutate((p) => {
        if (i < p.elements.length - 1) p.elements.splice(i + 1, 0, p.elements.splice(i, 1)[0]);
      });
    }));
    head.appendChild(mk("✕", "delete", () => {
      this.remapExpanded((e) => (e === i ? -1 : e > i ? e - 1 : e));
      this.mutate((p) => p.elements.splice(i, 1));
    }));

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

    const colorWrap = document.createElement("div");
    colorWrap.className = "fcore-sl";
    const cl = document.createElement("label");
    cl.textContent = "color";
    const col = document.createElement("input");
    col.type = "color";
    col.value = colorToHex(elem.color || [1, 1, 1]);
    col.addEventListener("pointerdown", (e) => e.stopPropagation());
    col.addEventListener("input", () => set("color", hexToColor(col.value)));
    colorWrap.append(cl, col);
    adv.appendChild(colorWrap);

    const arWrap = document.createElement("div");
    arWrap.className = "fcore-sl";
    const al = document.createElement("label");
    al.textContent = "auto-rotate";
    const ar = document.createElement("input");
    ar.type = "checkbox";
    ar.checked = elem.auto_rotate !== false;
    ar.addEventListener("pointerdown", (e) => e.stopPropagation());
    ar.onchange = () => set("auto_rotate", ar.checked);
    arWrap.append(al, ar);
    adv.appendChild(arWrap);

    for (const [key, spec] of Object.entries(COMMON_SPECS)) {
      adv.appendChild(slider(key.replace(/_/g, " "), elem[key], spec,
        (v) => set(key, spec[2] >= 1 ? Math.round(v) : v)));
    }
    adv.appendChild(slider("stretch x", elem.stretch?.[0] ?? 1, [0.1, 4, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].stretch || [1, 1];
        p.elements[i].stretch = [v, s[1]];
      })));
    adv.appendChild(slider("stretch y", elem.stretch?.[1] ?? 1, [0.1, 4, 0.01],
      (v) => this.mutateQuiet((p) => {
        const s = p.elements[i].stretch || [1, 1];
        p.elements[i].stretch = [s[0], v];
      })));

    const dropdown = (label, value, options, onPick) => {
      const wrap = document.createElement("div");
      wrap.className = "fcore-sl";
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
      const opts = this.libraryFiles.length ? this.libraryFiles : ["<library empty>"];
      adv.appendChild(dropdown("file", elem.params?.file || opts[0], opts,
        (v) => setParam("file", v)));
      adv.appendChild(dropdown("channel", elem.params?.channel || "auto",
        ["auto", "rgb", "luminance"], (v) => setParam("channel", v)));
    } else {
      if (elem.type === "spectral") {
        adv.appendChild(dropdown("shape", elem.params?.shape || "ring",
          ["ring", "iris"], (v) => setParam("shape", v)));
      }
      for (const [key, spec] of Object.entries(PARAM_SPECS[elem.type] || {})) {
        adv.appendChild(slider(key.replace(/_/g, " "), elem.params?.[key], spec,
          (v) => setParam(key, spec[2] >= 1 ? Math.round(v) : v)));
      }
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
      pickerWidget.computeSize = (w) => {
        const width = Number(w) || node.size?.[0] || 460;
        return [width, Math.min(Math.max((width - 20) * 0.5, 170), 300)];
      };

      const editor = new FlareEditor(node);
      node._fcEditor = editor;
      const editorWidget = node.addDOMWidget("flare_editor", "flarecore.editor",
        editor.root, { serialize: false, hideOnZoom: true, getMinHeight: () => 200 });
      editorWidget.serialize = false;
      editorWidget.serializeValue = () => undefined;
      editorWidget.computeSize = (w) => [Number(w) || node.size?.[0] || 460, 300];

      // The panels stay at the END of node.widgets. widgets_values is a
      // positional array and ComfyUI serializes a null for each DOM widget
      // rather than skipping it, so moving them to the front shifts every
      // real value two slots on reload — position_mode's "manual" lands in
      // detect_max_lights and so on. Keeping them last leaves the real
      // widgets at the indices every saved workflow already uses.
      setAdvanced(node, false);
      node.setSize([Math.max(node.size[0], 460), node.computeSize()[1]]);
      setTimeout(() => picker.draw(), 60);
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
      const imgs = message?.images;
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
