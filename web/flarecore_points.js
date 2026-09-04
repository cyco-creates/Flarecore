// SPDX-License-Identifier: Apache-2.0
// flarecore: draggable light / flare-anchor points on the FlareRender node.
// A DOM <canvas> widget: shows the last rendered frame as backdrop and writes
// straight into the light_x/light_y/flare_x/flare_y number widgets.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

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

class PointPicker {
  constructor(node) {
    this.node = node;
    this.backdrop = null;
    this.drag = null;
    this.el = document.createElement("div");
    this.el.style.cssText =
      "width:100%;height:100%;background:#101014;border:1px solid #333;" +
      "border-radius:6px;overflow:hidden;position:relative;box-sizing:border-box;";
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "width:100%;height:100%;display:block;cursor:crosshair;";
    this.el.appendChild(this.canvas);

    this.canvas.addEventListener("pointerdown", (e) => this.onDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.onMove(e));
    this.canvas.addEventListener("pointerup", (e) => this.onUp(e));
    this.canvas.addEventListener("pointercancel", (e) => this.onUp(e));

    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => this.draw()).observe(this.el);
    }
    // catch numeric-widget edits made outside the picker
    this._poll = setInterval(() => {
      if (!document.body.contains(this.el)) { clearInterval(this._poll); return; }
      const sig = ["light_x", "light_y", "flare_x", "flare_y"]
        .map((n) => getVal(this.node, n, 0)).join("|");
      if (sig !== this._sig) { this._sig = sig; this.draw(); }
    }, 400);
  }

  imageRect() {
    const W = this.canvas.width;
    const H = this.canvas.height;
    const aspect = this.backdrop ? this.backdrop.width / this.backdrop.height : 16 / 9;
    let w = W, h = w / aspect;
    if (h > H) { h = H; w = h * aspect; }
    return { x: (W - w) / 2, y: (H - h) / 2, w, h };
  }

  pointPx(name_x, name_y, r) {
    return [
      r.x + getVal(this.node, name_x, 0.5) * r.w,
      r.y + getVal(this.node, name_y, 0.5) * r.h,
    ];
  }

  draw() {
    const rect = this.el.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return;
    if (this.canvas.width !== Math.round(rect.width) ||
        this.canvas.height !== Math.round(rect.height)) {
      this.canvas.width = Math.round(rect.width);
      this.canvas.height = Math.round(rect.height);
    }
    const ctx = this.canvas.getContext("2d");
    const W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#101014";
    ctx.fillRect(0, 0, W, H);

    const r = this.imageRect();
    if (this.backdrop) {
      ctx.drawImage(this.backdrop, r.x, r.y, r.w, r.h);
    } else {
      ctx.fillStyle = "#555";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("render once for a backdrop — the points already work",
        W / 2, H / 2);
      ctx.textAlign = "left";
    }

    const [lx, ly] = this.pointPx("light_x", "light_y", r);
    const [fx, fy] = this.pointPx("flare_x", "flare_y", r);

    // axis: solid to the anchor, dashed ghost tail past it
    ctx.lineWidth = 1.4;
    ctx.strokeStyle = "rgba(255,255,255,0.6)";
    ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(fx, fy); ctx.stroke();
    ctx.setLineDash([5, 5]);
    ctx.strokeStyle = "rgba(255,255,255,0.3)";
    ctx.beginPath(); ctx.moveTo(fx, fy);
    ctx.lineTo(fx + (fx - lx), fy + (fy - ly)); ctx.stroke();
    ctx.setLineDash([]);
    for (let i = 1; i < 4; i++) {
      const t = i / 4;
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.beginPath();
      ctx.arc(lx + (fx - lx) * t, ly + (fy - ly) * t, 1.7, 0, Math.PI * 2);
      ctx.fill();
    }

    // light: orange sun
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

    // anchor: cyan ring + crosshair
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
    ctx.fillText("light", lx + 13, ly - 9);
    ctx.fillStyle = "#5fd7ff";
    ctx.fillText("flare anchor", fx + 13, fy + 18);
  }

  eventPos(e) {
    const rect = this.canvas.getBoundingClientRect();
    return [
      ((e.clientX - rect.left) / rect.width) * this.canvas.width,
      ((e.clientY - rect.top) / rect.height) * this.canvas.height,
    ];
  }

  onDown(e) {
    const [x, y] = this.eventPos(e);
    const r = this.imageRect();
    const [lx, ly] = this.pointPx("light_x", "light_y", r);
    const [fx, fy] = this.pointPx("flare_x", "flare_y", r);
    const dl = Math.hypot(x - lx, y - ly);
    const df = Math.hypot(x - fx, y - fy);
    if (Math.min(dl, df) > 26) return;
    this.drag = dl <= df ? "light" : "flare";
    this.canvas.setPointerCapture(e.pointerId);
    e.stopPropagation();
    e.preventDefault();
  }

  onMove(e) {
    if (!this.drag) return;
    const [x, y] = this.eventPos(e);
    const r = this.imageRect();
    const u = Math.min(1, Math.max(0, (x - r.x) / r.w));
    const v = Math.min(1, Math.max(0, (y - r.y) / r.h));
    setVal(this.node, this.drag === "light" ? "light_x" : "flare_x", u);
    setVal(this.node, this.drag === "light" ? "light_y" : "flare_y", v);
    this.node.setDirtyCanvas(true, false);
    this.draw();
    e.stopPropagation();
  }

  onUp(e) {
    if (this.drag) {
      this.drag = null;
      try { this.canvas.releasePointerCapture(e.pointerId); } catch {}
      e.stopPropagation();
    }
  }

  setBackdrop(img) {
    this.backdrop = img;
    this.draw();
  }
}

app.registerExtension({
  name: "flarecore.points",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "FlareRender") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onNodeCreated?.apply(this, arguments);
      const node = this;
      const picker = new PointPicker(node);
      node._fcPicker = picker;

      const widget = node.addDOMWidget("flare_layout", "flarecore.layout",
        picker.el, { serialize: false, getMinHeight: () => 180 });
      widget.serialize = false;
      widget.serializeValue = () => undefined;
      // the frontend sometimes calls computeSize() with no argument
      widget.computeSize = (w) => {
        const width = Number(w) || node.size?.[0] || 440;
        return [width, Math.min((width * 9) / 16 + 12, 330)];
      };
      setTimeout(() => picker.draw(), 50);
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
