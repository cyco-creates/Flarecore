// SPDX-License-Identifier: Apache-2.0
// flarecore: the flare stack editor. A DOM widget on FlareRender that edits
// the preset_json widget — every element gets sliders for position along the
// axis, size, and opacity, with the full parameter set one twirl-down away.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

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

// [min, max, step] per advanced numeric field
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
      if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("pointerdown", close); }
    };
    document.addEventListener("pointerdown", close);
  }, 0);
}

function slider(label, value, [min, max, step], onChange, width) {
  const wrap = document.createElement("div");
  wrap.className = "fcore-sl";
  if (width) wrap.style.flex = `0 0 ${width}px`;
  const lab = document.createElement("label");
  lab.textContent = label;
  const inp = document.createElement("input");
  inp.type = "range"; inp.min = min; inp.max = max; inp.step = step;
  inp.value = value ?? min;
  const out = document.createElement("output");
  const show = (v) => { out.textContent = Number(v).toFixed(step >= 1 ? 0 : 2); };
  show(inp.value);
  inp.addEventListener("input", () => { show(inp.value); onChange(Number(inp.value)); });
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
    this.fetchLibrary();
    this.build();
  }

  get widget() {
    return this.node.widgets?.find((w) => w.name === "preset_json");
  }

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
    if (this.widget) {
      this.widget.value = text;
      this.lastText = text;
    }
    this.node.setDirtyCanvas(true, false);
  }

  mutate(fn) {
    const preset = this.read();
    if (!preset) return;
    fn(preset);
    this.write(preset);
    this.build();
  }

  async fetchLibrary() {
    try {
      const r = await api.fetchApi("/flarecore/elements");
      const d = await r.json();
      this.libraryFiles = d.elements || [];
    } catch (e) { this.libraryFiles = []; }
  }

  build() {
    this.root.textContent = "";
    const preset = this.read();

    // toolbar -----------------------------------------------------------
    const bar = document.createElement("div");
    bar.className = "fcore-bar";
    const addBtn = document.createElement("button");
    addBtn.className = "fcore-btn accent";
    addBtn.textContent = "+ add element";
    addBtn.onclick = (e) => popupMenu(e, ADD_MENU, (type) => {
      const elem = JSON.parse(JSON.stringify(ADD_DEFAULTS[type]));
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
            const rr = await api.fetchApi(`/flarecore/preset/${name.replace(/\.json$/, "")}`);
            const dd = await rr.json();
            if (dd.json && this.widget) {
              this.widget.value = dd.json;
              this.build();
            }
          });
      } catch (err) { console.error("flarecore presets", err); }
    };

    const saveBtn = document.createElement("button");
    saveBtn.className = "fcore-btn";
    saveBtn.textContent = "save…";
    saveBtn.onclick = async () => {
      const name = prompt("Preset name:", "my_flare");
      if (!name) return;
      try {
        const r = await api.fetchApi("/flarecore/save_preset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, json: this.widget?.value || "" }),
        });
        const d = await r.json();
        saveBtn.textContent = d.saved ? "saved ✓" : `error: ${d.error}`;
      } catch (e) { saveBtn.textContent = "error"; }
      setTimeout(() => { saveBtn.textContent = "save…"; }, 1800);
    };

    const renderBtn = document.createElement("button");
    renderBtn.className = "fcore-btn accent";
    renderBtn.textContent = "▶ render";
    renderBtn.onclick = () => app.queuePrompt(0);

    bar.append(addBtn, loadBtn, saveBtn, renderBtn);
    if (this.error) {
      const badge = document.createElement("span");
      badge.className = "fcore-badge";
      badge.textContent = "⚠ invalid JSON in preset_json";
      bar.appendChild(badge);
    }
    this.root.appendChild(bar);
    if (!preset) return;

    // global row --------------------------------------------------------
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
    tint.addEventListener("input", () =>
      this.mutateQuiet((p) => { p.global.tint = hexToColor(tint.value); }));
    gRow.appendChild(tint);
    this.root.appendChild(gRow);

    // element rows ------------------------------------------------------
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

  // change values without rebuilding the DOM (used by sliders)
  mutateQuiet(fn) {
    const preset = this.read();
    if (!preset) return;
    fn(preset);
    this.write(preset);
  }

  buildRow(elem, i) {
    const row = document.createElement("div");
    row.className = "fcore-row" + (elem.enabled === false ? " off" : "");

    const head = document.createElement("div");
    head.className = "fcore-head";

    const en = document.createElement("input");
    en.type = "checkbox";
    en.checked = elem.enabled !== false;
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
    head.appendChild(mk("⧉", "duplicate", () => this.mutate((p) =>
      p.elements.splice(i + 1, 0, JSON.parse(JSON.stringify(p.elements[i]))))));
    head.appendChild(mk("↑", "move up", () => { if (i > 0) this.mutate((p) =>
      p.elements.splice(i - 1, 0, p.elements.splice(i, 1)[0])); }));
    head.appendChild(mk("↓", "move down", () => this.mutate((p) => {
      if (i < p.elements.length - 1) p.elements.splice(i + 1, 0, p.elements.splice(i, 1)[0]);
    })));
    head.appendChild(mk("✕", "delete", () => this.mutate((p) =>
      p.elements.splice(i, 1))));

    row.appendChild(head);

    if (this.expanded.has(i)) row.appendChild(this.buildAdvanced(elem, i));
    return row;
  }

  buildAdvanced(elem, i) {
    const adv = document.createElement("div");
    adv.className = "fcore-adv";
    const set = (key, v) => this.mutateQuiet((p) => { p.elements[i][key] = v; });
    const setParam = (key, v) => this.mutateQuiet((p) => {
      if (!p.elements[i].params) p.elements[i].params = {};
      p.elements[i].params[key] = v;
    });

    // colour + auto-rotate
    const colorWrap = document.createElement("div");
    colorWrap.className = "fcore-sl";
    const cl = document.createElement("label");
    cl.textContent = "color";
    const col = document.createElement("input");
    col.type = "color";
    col.value = colorToHex(elem.color || [1, 1, 1]);
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

    // per-type params
    if (elem.type === "texture") {
      const fileWrap = document.createElement("div");
      fileWrap.className = "fcore-sl";
      const fl = document.createElement("label");
      fl.textContent = "file";
      const sel = document.createElement("select");
      const opts = this.libraryFiles.length ? this.libraryFiles : ["<library empty>"];
      for (const f of opts) {
        const o = document.createElement("option");
        o.value = f;
        o.textContent = f;
        sel.appendChild(o);
      }
      sel.value = elem.params?.file || opts[0];
      sel.onchange = () => setParam("file", sel.value);
      fileWrap.append(fl, sel);
      adv.appendChild(fileWrap);

      const chWrap = document.createElement("div");
      chWrap.className = "fcore-sl";
      const chl = document.createElement("label");
      chl.textContent = "channel";
      const ch = document.createElement("select");
      for (const c of ["auto", "rgb", "luminance"]) {
        const o = document.createElement("option");
        o.value = c;
        o.textContent = c;
        ch.appendChild(o);
      }
      ch.value = elem.params?.channel || "auto";
      ch.onchange = () => setParam("channel", ch.value);
      chWrap.append(chl, ch);
      adv.appendChild(chWrap);
    } else {
      if (elem.type === "spectral") {
        const shWrap = document.createElement("div");
        shWrap.className = "fcore-sl";
        const shl = document.createElement("label");
        shl.textContent = "shape";
        const sh = document.createElement("select");
        for (const s of ["ring", "iris"]) {
          const o = document.createElement("option");
          o.value = s;
          o.textContent = s;
          sh.appendChild(o);
        }
        sh.value = elem.params?.shape || "ring";
        sh.onchange = () => setParam("shape", sh.value);
        shWrap.append(shl, sh);
        adv.appendChild(shWrap);
      }
      const specs = PARAM_SPECS[elem.type] || {};
      for (const [key, spec] of Object.entries(specs)) {
        adv.appendChild(slider(key.replace(/_/g, " "), elem.params?.[key], spec,
          (v) => setParam(key, spec[2] >= 1 ? Math.round(v) : v)));
      }
    }
    return adv;
  }
}

app.registerExtension({
  name: "flarecore.editor",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "FlareRender") return;
    injectCSS();

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onNodeCreated?.apply(this, arguments);
      const node = this;
      const editor = new FlareEditor(node);
      node._fcEditor = editor;

      const widget = node.addDOMWidget("flare_editor", "flarecore.editor",
        editor.root, {
          serialize: false,
          getMinHeight: () => 240,
        });
      widget.serialize = false;
      widget.serializeValue = () => undefined;
      widget.computeSize = (w) => [w, 300];

      // pick up external edits of the raw preset_json textarea
      const poll = setInterval(() => {
        if (!document.body.contains(editor.root)) { clearInterval(poll); return; }
        const text = editor.widget?.value;
        if (text !== editor.lastText) {
          editor.lastText = text;
          editor.build();
        }
      }, 700);

      node.setSize([Math.max(node.size[0], 420), node.computeSize()[1]]);
    };
  },
});
