// Change chart of one segment: frame-to-frame change, change vs. last keyframe,
// draggable threshold line, markers of the selected frames, hover thumbnails.
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}, parent = null) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

function niceMax(v) {
  if (v <= 1) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function markerClass(kinds) {
  if (kinds.includes("trigger")) return "trigger";
  if (kinds.includes("first") || kinds.includes("last")) return "edge";
  return "";
}

class SelectionChart {
  constructor(container, { editable = false, onThreshold = null, thumb = null } = {}) {
    this.W = 720; this.H = 230;
    this.m = { l: 40, r: 12, t: 14, b: 26 };
    this.editable = editable;
    this.onThreshold = onThreshold;
    this.thumb = thumb;
    this.dragging = false;
    this.box = document.createElement("div");
    this.box.className = "chart-box" + (editable ? "" : " readonly");
    container.appendChild(this.box);
    this.svg = svgEl("svg", { viewBox: `0 0 ${this.W} ${this.H}`, role: "img" }, this.box);
    this.gBase = svgEl("g", {}, this.svg);   // grid, curves, markers – redrawn on every update
    this.gHover = svgEl("g", {}, this.svg);  // cursor + hover capture
    this.gThr = svgEl("g", {}, this.svg);    // threshold – kept while dragging
    this.tip = document.createElement("div");
    this.tip.className = "chart-tip hidden";
    this.box.appendChild(this.tip);
    this.buildHover();
  }

  x(t) { const { l, r } = this.m; return l + (t - this.t0) / (this.t1 - this.t0 || 1) * (this.W - l - r); }
  y(v) { const { t, b } = this.m; return this.H - b - (v / this.yMax) * (this.H - t - b); }
  yInv(py) { const { t, b } = this.m; return (this.H - b - py) / (this.H - t - b) * this.yMax; }

  update(data) {
    this.data = data;
    const c = data.chart;
    this.t0 = c.t[0] ?? 0;
    this.t1 = c.t[c.t.length - 1] ?? 1;
    if (!this.dragging) {
      // robust scale: a single huge spike must not flatten the region where the threshold lives
      const values = [...c.motion, ...c.ref].sort((a, b) => a - b);
      const p98 = values.length ? values[Math.floor((values.length - 1) * 0.98)] : 0;
      this.yMax = niceMax(Math.max(1, data.threshold * 2, p98) * 1.12);
    }
    this.selected = new Map(data.frames.map((f) => [f.index, f]));
    this.renderBase();
    if (!this.dragging) this.renderThreshold(data.threshold, data.threshold_source || "");
  }

  renderBase() {
    const g = this.gBase;
    g.replaceChildren();
    const { l, r, t, b } = this.m;
    const c = this.data.chart;
    for (let i = 0; i <= 4; i++) {
      const v = (this.yMax / 4) * i, yy = this.y(v);
      svgEl("line", { x1: l, x2: this.W - r, y1: yy, y2: yy, class: i ? "gridline" : "axis" }, g);
      svgEl("text", { x: l - 6, y: yy + 3, "text-anchor": "end", class: "tick" }, g).textContent = `${+v.toFixed(1)}%`;
    }
    const span = this.t1 - this.t0;
    const step = span > 20 ? 5 : span > 6 ? 1 : 0.5;
    for (let s = 0; s <= span + 1e-6; s += step) {
      svgEl("text", { x: this.x(this.t0 + s), y: this.H - 8, "text-anchor": "middle", class: "tick" }, g)
        .textContent = `${+s.toFixed(1)}s`;
    }
    for (const f of this.data.frames) {
      const cls = markerClass(f.kinds);
      const xx = this.x(f.t);
      svgEl("line", { x1: xx, x2: xx, y1: t, y2: this.H - b, class: `mark ${cls}` }, g);
      svgEl("circle", { cx: xx, cy: t, r: cls ? 3.5 : 2.5, class: `mark-dot ${cls}` }, g);
    }
    const pts = (values) => values.map((v, i) =>
      `${this.x(c.t[i]).toFixed(1)},${this.y(Math.min(v, this.yMax)).toFixed(1)}`).join(" ");
    svgEl("polyline", { points: pts(c.ref), class: "ref" }, g);
    svgEl("polyline", { points: pts(c.motion), class: "motion" }, g);
  }

  renderThreshold(value, source) {
    const g = this.gThr;
    g.replaceChildren();
    const { l, r } = this.m;
    const yy = this.y(Math.min(value, this.yMax));
    this.thrLine = svgEl("line", { x1: l, x2: this.W - r, y1: yy, y2: yy, class: "thr" }, g);
    this.thrLabel = svgEl("text", { x: this.W - r - 4, y: yy - 5, "text-anchor": "end", class: "thr-label" }, g);
    this.thrLabel.textContent = `${value.toFixed(2)}% ${source}`;
    this.thrHit = svgEl("line", { x1: l, x2: this.W - r, y1: yy, y2: yy, class: "thr-hit" }, g);
    if (this.editable) {
      this.thrHit.addEventListener("pointerdown", (ev) => this.startDrag(ev));
      const title = svgEl("title", {}, this.thrHit);
      title.textContent = "Drag to change the threshold";
    }
  }

  buildHover() {
    const { l, r, t, b } = this.m;
    this.cursor = svgEl("line", { x1: 0, x2: 0, y1: t, y2: this.H - b, class: "cursor", visibility: "hidden" }, this.gHover);
    const rect = svgEl("rect", { x: l, y: t, width: this.W - l - r, height: this.H - t - b, fill: "transparent" }, this.gHover);
    rect.addEventListener("pointermove", (ev) => this.hover(ev));
    rect.addEventListener("pointerleave", () => this.unhover());
  }

  svgPoint(ev) {
    const rect = this.svg.getBoundingClientRect();
    return { x: (ev.clientX - rect.left) * this.W / rect.width, y: (ev.clientY - rect.top) * this.H / rect.height };
  }

  startDrag(ev) {
    ev.preventDefault();
    this.dragging = true;
    this.unhover();
    const target = ev.target;
    target.setPointerCapture(ev.pointerId);
    const value = (e) => Math.max(0, Math.min(this.yMax, this.yInv(this.svgPoint(e).y)));
    const move = (e) => {
      const v = value(e);
      this.moveThreshold(v);
      if (this.onThreshold) this.onThreshold(v, false);
    };
    const up = (e) => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      const v = value(e);
      this.dragging = false;
      if (this.onThreshold) this.onThreshold(v, true);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  }

  moveThreshold(v) {
    const yy = this.y(v);
    for (const el of [this.thrLine, this.thrHit]) { el.setAttribute("y1", yy); el.setAttribute("y2", yy); }
    this.thrLabel.setAttribute("y", yy - 5);
    this.thrLabel.textContent = `${v.toFixed(2)}% manual`;
  }

  hover(ev) {
    if (this.dragging || !this.data) return;
    const c = this.data.chart;
    const p = this.svgPoint(ev);
    const tt = this.t0 + (p.x - this.m.l) / (this.W - this.m.l - this.m.r) * (this.t1 - this.t0);
    let idx = 0, best = Infinity;
    for (let i = 0; i < c.t.length; i++) {
      const d = Math.abs(c.t[i] - tt);
      if (d < best) { best = d; idx = i; }
    }
    const xx = this.x(c.t[idx]);
    this.cursor.setAttribute("x1", xx);
    this.cursor.setAttribute("x2", xx);
    this.cursor.setAttribute("visibility", "visible");
    const sel = this.selected.get(idx);
    this.tip.innerHTML = `${this.thumb ? `<img src="${this.thumb(idx, c.t[idx])}" alt="">` : ""}
      <div><b>${c.t[idx].toFixed(2)} s</b>${sel ? " · " + esc(sel.kinds.join(", ")) : ""}</div>
      <div class="muted">Δ prev ${c.motion[idx].toFixed(2)}% · Δ key ${c.ref[idx].toFixed(2)}%</div>`;
    this.tip.classList.remove("hidden");
    const rect = this.svg.getBoundingClientRect();
    const px = xx * rect.width / this.W;
    this.tip.style.left = `${Math.min(Math.max(px - 88, 0), Math.max(rect.width - 180, 0))}px`;
    this.tip.style.top = "-8px";
    this.tip.style.transform = "translateY(-100%)";
  }

  unhover() {
    this.tip.classList.add("hidden");
    this.cursor.setAttribute("visibility", "hidden");
  }
}

function renderFilmstrip(el, frames, thumb) {
  el.innerHTML = frames.map((f) => {
    const cls = markerClass(f.kinds);
    return `<figure class="${cls}" title="${esc(f.kinds.join(", "))} · score ${f.score}">
      <img loading="lazy" src="${thumb(f.index, f.t)}" alt="">
      <figcaption>${f.t.toFixed(2)}s · ${esc(f.kinds[0])}</figcaption></figure>`;
  }).join("");
}

function chartLegend() {
  return `<div class="legend">
    <span><i style="background:var(--c-motion)"></i>change vs. previous frame</span>
    <span><i style="background:var(--c-ref)"></i>change vs. last keyframe</span>
    <span><i style="background:var(--c-threshold)"></i>threshold (drag)</span>
    <span><i style="background:var(--c-trigger)"></i>trigger</span>
    <span><i style="background:var(--c-edge)"></i>first / last</span>
    <span><i style="background:var(--c-pick)"></i>selected</span></div>`;
}
