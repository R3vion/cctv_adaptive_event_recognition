// Looping preview of selected frames: one loop always takes 3 s, so the speed follows the
// frame count (30 frames → 10 fps, 10 frames → 3.3 fps). Used on the import and camera pages.
"use strict";

const PREVIEW_LOOP_MS = 3000;

class KeyframePlayer {
  // frame objects need { t, kinds }; `url(frame)` returns the image URL, `full(frame)` (optional)
  // the full-resolution URL opened when the image is clicked.
  constructor(container, { full = null } = {}) {
    this.el = container;
    this.el.classList.add("kf-player");
    this.el.innerHTML = `
      <img alt="selected frames preview"><span class="kf-badge"></span>
      <div class="kf-bar"><div></div></div>
      <div class="small muted kf-info"></div>`;
    this.img = this.el.querySelector("img");
    this.full = full;
    if (full) {
      this.img.style.cursor = "zoom-in";
      this.img.title = "Open this frame in full resolution";
      this.img.addEventListener("click", () => { const f = this.frames[this.shown]; if (f) window.open(full(f), "_blank"); });
    }
    this.frames = [];
    this.urls = [];
    this.pos = 0;
    this.shown = 0;
    this.timer = null;
  }

  get playing() { return this.timer !== null; }

  setFrames(frames, url) {
    this.frames = frames;
    this.urls = frames.map(url);
    this.urls.forEach((u) => { const img = new Image(); img.src = u; }); // preload so the loop runs smoothly
    this.pos = 0;
    if (this.playing) this.start(); // restart with the new frame count / speed
  }

  start() {
    clearInterval(this.timer);
    this.timer = null;
    const n = this.frames.length;
    if (!n) return;
    const badge = this.el.querySelector(".kf-badge");
    const bar = this.el.querySelector(".kf-bar > div");
    this.el.querySelector(".kf-info").textContent =
      `${n} frame${n === 1 ? "" : "s"} · ${(n / (PREVIEW_LOOP_MS / 1000)).toFixed(1)} fps · one loop = 3 s`;
    const show = () => {
      const f = this.frames[this.pos];
      this.img.src = this.urls[this.pos];
      badge.textContent = `${this.pos + 1}/${n} · ${f.t.toFixed(2)} s · ${f.kinds.join(", ")}`;
      bar.style.width = `${((this.pos + 1) / n) * 100}%`;
      this.shown = this.pos;
      this.pos = (this.pos + 1) % n;
    };
    show();
    this.timer = setInterval(show, PREVIEW_LOOP_MS / n);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }
}
