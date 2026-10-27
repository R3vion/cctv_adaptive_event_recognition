// Import page: upload → segment cards (trigger slider, change chart, filmstrip) → save.
"use strict";

const CAMERA_RE = /^[A-Za-z0-9_-]{1,64}$/;
const $ = (id) => document.getElementById(id);

const state = {
  cameras: [],
  episodeGap: 15,
  session: null,
  selection: [],
  tsMode: "today",
  cards: [],
  seq: {},          // latest request number per segment (ignore stale answers)
};

// --- time helpers -----------------------------------------------------------------
const pad = (n) => String(n).padStart(2, "0");
function toInput(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function toDisplay(d) {
  return `${d.getFullYear()}_${pad(d.getMonth() + 1)}_${pad(d.getDate())}-${pad(d.getHours())}_${pad(d.getMinutes())}_${pad(d.getSeconds())}`;
}
function parseLocal(value) {
  if (!value) return null;
  const v = value.length === 16 ? value + ":00" : value;
  const d = new Date(v);
  return isNaN(d) ? null : d;
}

// --- cameras ----------------------------------------------------------------------
async function loadCameras() {
  const data = await api("/api/import/cameras");
  state.cameras = data.cameras;
  state.episodeGap = data.episode_gap_s;
  $("camera-list").innerHTML = state.cameras
    .map((c) => `<option value="${esc(c.camera_id)}">${esc(c.camera_id)} – ${c.events} events</option>`).join("");
}
function existingCamera() {
  const id = $("camera-id").value.trim();
  return state.cameras.find((c) => c.camera_id === id) || null;
}
function firstNumber() {
  const cam = existingCamera();
  return cam ? cam.last_event_no + 1 : 1;
}

function defaultStart() {
  const cam = existingCamera();
  const now = new Date();
  now.setMilliseconds(0);
  if (state.tsMode === "today") {
    if (!cam) return now;
    return new Date(new Date(cam.last_timestamp_iso).getTime() + 10000);
  }
  if (!cam) {
    const day = new Date(now.getTime() - Math.floor(Math.random() * 365) * 86400000);
    day.setHours(Math.floor(Math.random() * 24), Math.floor(Math.random() * 60), Math.floor(Math.random() * 60));
    return day > now ? now : day;
  }
  const after = new Date(cam.last_timestamp_iso).getTime() + 10000;
  const until = Math.max(now.getTime(), after + 86400000);
  return new Date(after + Math.floor(Math.random() * (until - after) / 1000) * 1000);
}

function setStart(d) { $("start-ts").value = toInput(d); refreshAll(); }

// --- notes, ids, timestamps -----------------------------------------------------------
function refreshAll() {
  if (!state.session) return;
  const id = $("camera-id").value.trim();
  const cam = existingCamera();
  const camNote = $("camera-note");
  const start = parseLocal($("start-ts").value);
  let ok = true;

  if (!CAMERA_RE.test(id)) {
    camNote.className = "note err";
    camNote.textContent = "Camera id: letters, digits, '_' and '-' only (max 64).";
    ok = false;
  } else if (cam) {
    camNote.className = "note info";
    camNote.textContent = `Existing camera – ${cam.events} events, last ${cam.camera_id}#${pad3(cam.last_event_no)} at ${cam.last_timestamp}. Numbering continues at #${pad3(cam.last_event_no + 1)}.`;
  } else {
    camNote.className = "note";
    camNote.textContent = "New camera – events will be numbered from #001.";
  }

  const epNote = $("episode-note");
  if (!start) {
    epNote.className = "note err"; epNote.textContent = "Enter a valid start timestamp."; ok = false;
  } else if (cam) {
    const gap = (start.getTime() - new Date(cam.last_timestamp_iso).getTime()) / 1000;
    if (gap <= 0) {
      epNote.className = "note err"; epNote.textContent = `The start must be later than ${cam.last_timestamp}.`; ok = false;
    } else if (gap <= state.episodeGap) {
      epNote.className = "note ok";
      epNote.textContent = `Joins the camera's last episode (gap ${fmtDuration(gap)} ≤ ${state.episodeGap} s).`;
    } else {
      epNote.className = "note warn";
      epNote.textContent = `Opens a new episode (gap ${fmtDuration(gap)} > ${state.episodeGap} s).`;
    }
  } else {
    epNote.className = "note"; epNote.textContent = "First episode of this camera.";
  }

  const first = firstNumber();
  state.cards.forEach((card, i) => {
    const ts = start ? toDisplay(new Date(start.getTime() + i * 10000)) : "–";
    card.title.textContent = `${CAMERA_RE.test(id) ? id : "?"}#${pad3(first + i)}`;
    card.ts.textContent = ts;
  });
  const n = state.cards.length;
  $("save-summary").textContent = ok
    ? `${n} event${n > 1 ? "s" : ""}: ${id}#${pad3(first)}${n > 1 ? " … #" + pad3(first + n - 1) : ""}`
    : "Fix the highlighted fields to save.";
  $("btn-save").disabled = !ok;
}
const pad3 = (n) => String(n).padStart(3, "0");

// --- upload --------------------------------------------------------------------
function upload(file) {
  if (!file) return;
  if (!/\.mp4$/i.test(file.name)) { toast("Only .mp4 files can be imported.", "err"); return; }
  $("upload-progress").classList.remove("hidden");
  $("upload-label").textContent = `Uploading ${file.name}…`;
  $("upload-bar").style.width = "0";
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/import/upload");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    $("upload-bar").style.width = pct + "%";
    $("upload-pct").textContent = pct + "%";
    if (pct >= 100) $("upload-label").textContent = "Analysing the video (change signals, frame selection)…";
  };
  xhr.onload = () => {
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch { /* keep null */ }
    $("upload-progress").classList.add("hidden");
    if (xhr.status !== 200) {
      toast(data && data.detail ? data.detail : `Upload failed (${xhr.status})`, "err", 7000);
      return;
    }
    startSession(data);
  };
  xhr.onerror = () => { $("upload-progress").classList.add("hidden"); toast("Upload failed.", "err"); };
  const form = new FormData();
  form.append("file", file);
  xhr.send(form);
}

async function startSession(data) {
  state.session = data;
  state.selection = data.selection;
  await loadCameras();
  $("drop-section").classList.add("hidden");
  $("session-section").classList.remove("hidden");
  $("session-title").textContent = data.filename;
  const v = data.video;
  $("video-info").textContent = `${v.width}×${v.height} · ${v.fps} fps · ${v.duration_s.toFixed(1)} s · ${data.segments.length} segment(s)`;
  $("camera-id").value = data.suggested_camera_id;
  state.tsMode = "today";
  markMode();
  renderSegments();
  setStart(defaultStart());
}

function resetToDrop() {
  state.cards.forEach(stopPreview);
  state.session = null;
  state.cards = [];
  $("segments").innerHTML = "";
  $("session-section").classList.add("hidden");
  $("done-section").classList.add("hidden");
  $("drop-section").classList.remove("hidden");
  $("file-input").value = "";
}

// --- segment cards ------------------------------------------------------------------
function thumbUrl(sel) {
  return (i) => `/api/import/sessions/${state.session.id}/thumb/${sel.grid_offset + i}`;
}

function renderSegments() {
  const box = $("segments");
  box.innerHTML = "";
  state.cards = [];
  const sid = state.session.id;
  const lazy = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && !e.target.src) { e.target.src = `/api/import/sessions/${sid}/video`; lazy.unobserve(e.target); }
    }
  }, { rootMargin: "300px" });

  state.session.segments.forEach((seg, i) => {
    const sel = state.selection[i];
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `
      <div class="row spread" style="margin-bottom:10px">
        <h3 style="margin:0"><span class="seg-title"></span> <span class="muted small seg-ts"></span></h3>
        <span class="muted small">segment ${i + 1} · ${seg.start_s.toFixed(1)}–${(seg.start_s + seg.duration_s).toFixed(1)} s of the video</span>
      </div>
      <div class="segment-card">
        <div class="stack">
          <video muted playsinline preload="metadata"></video>
          <input type="range" min="0" max="${seg.duration_s}" step="${(1 / state.session.video.fps).toFixed(4)}" value="${sel.trigger_s}">
          <dl class="kv">
            <dt>Trigger</dt><dd class="trig"></dd>
            <dt>Duration</dt><dd>${seg.duration_s.toFixed(2)} s</dd>
          </dl>
          <button class="btn small kf-toggle" title="Loops the selected frames – what the model will see – in 3 seconds">▶ Show preview</button>
          <div class="kf-holder hidden"></div>
        </div>
        <div class="stack">
          <div class="chart-holder"></div>
          <div class="row spread small">
            <span class="sel-info muted"></span>
            <button class="btn small reset-btn hidden">Reset to ${esc(sel.threshold_source === "manual" ? "default" : sel.threshold_source)} threshold</button>
          </div>
          <div class="filmstrip"></div>
        </div>
      </div>`;
    box.appendChild(el);

    const video = el.querySelector("video");
    const slider = el.querySelector("input[type=range]");
    const card = {
      el, video, slider, index: i, seg,
      title: el.querySelector(".seg-title"), ts: el.querySelector(".seg-ts"),
      trig: el.querySelector(".trig"), info: el.querySelector(".sel-info"),
      reset: el.querySelector(".reset-btn"), film: el.querySelector(".filmstrip"),
    };
    card.chart = new SelectionChart(el.querySelector(".chart-holder"), {
      editable: true,
      thumb: thumbUrl(sel),
      onThreshold: (v, final) => scheduleThreshold(i, v, final),
    });
    video.addEventListener("loadedmetadata", () => { video.currentTime = seg.start_s + Number(slider.value); });
    lazy.observe(video);
    slider.addEventListener("input", () => {
      card.trig.textContent = `${Number(slider.value).toFixed(2)} s`;
      if (video.readyState >= 1) video.currentTime = seg.start_s + Number(slider.value);
    });
    slider.addEventListener("change", () => sendUpdate(i, { trigger_s: Number(slider.value) }));
    card.reset.addEventListener("click", () => sendUpdate(i, { manual: false }));
    card.preview = new KeyframePlayer(el.querySelector(".kf-holder"));
    card.previewBtn = el.querySelector(".kf-toggle");
    card.previewBtn.addEventListener("click", () => togglePreview(card));
    state.cards.push(card);
    applySelection(sel);
  });
  $("legend").innerHTML = chartLegend();
}

function applySelection(sel) {
  state.selection[sel.index] = sel;
  const card = state.cards[sel.index];
  if (!card) return;
  card.chart.thumb = thumbUrl(sel);
  card.chart.update(sel);
  renderFilmstrip(card.film, sel.frames, thumbUrl(sel));
  card.trig.textContent = `${sel.trigger_s.toFixed(2)} s`;
  const over = sel.selected > 30 ? " · above the recommended 30" : "";
  card.info.innerHTML = `Threshold <b>${sel.threshold.toFixed(2)}%</b> (${esc(sel.threshold_source)}) · ` +
    `${sel.candidates} candidate${sel.candidates === 1 ? "" : "s"} → <b>${sel.selected}</b> frames selected${over}`;
  card.reset.classList.toggle("hidden", !sel.manual);
  const url = thumbUrl(sel);
  card.preview.setFrames(sel.frames, (f) => url(f.index));
}

// --- looping preview of the selected frames (KeyframePlayer, keyframes.js) ------------------
function stopPreview(card) {
  card.preview.stop();
}

function togglePreview(card) {
  const p = card.preview;
  const playing = p.playing;
  if (playing) p.stop(); else { p.pos = 0; p.start(); }
  p.el.classList.toggle("hidden", playing);
  card.previewBtn.textContent = playing ? "▶ Show preview" : "■ Hide preview";
}

const timers = {};
function scheduleThreshold(index, value, final) {
  clearTimeout(timers[index]);
  if (final) { sendUpdate(index, { threshold: value, manual: true }); return; }
  timers[index] = setTimeout(() => sendUpdate(index, { threshold: value, manual: true }), 110);
}

async function sendUpdate(index, body) {
  const seq = (state.seq[index] || 0) + 1;
  state.seq[index] = seq;
  try {
    const res = await api(`/api/import/sessions/${state.session.id}/selection`, { method: "POST", body: { index, ...body } });
    if (state.seq[index] !== seq) return; // a newer request for this segment is on its way
    for (const sel of res.segments) applySelection(sel);
  } catch (err) {
    toast(err.message, "err");
  }
}

// --- save -------------------------------------------------------------------------
async function save() {
  const btn = $("btn-save");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const body = {
      camera_id: $("camera-id").value.trim(),
      start: $("start-ts").value.length === 16 ? $("start-ts").value + ":00" : $("start-ts").value,
      segments: state.cards.map((c) => ({ index: c.index, trigger_offset_s: Number(c.slider.value) })),
    };
    const res = await api(`/api/import/sessions/${state.session.id}/commit`, { method: "POST", body });
    showDone(res);
  } catch (err) {
    toast(err.message, "err", 8000);
  } finally {
    btn.textContent = "Save events";
    btn.disabled = false;
  }
}

function showDone(res) {
  state.cards.forEach(stopPreview);
  state.session = null;
  $("session-section").classList.add("hidden");
  $("done-section").classList.remove("hidden");
  $("done-note").textContent = `Saved ${res.events.length} event(s) for camera ${res.camera_id}. The analytics worker picks them up automatically in auto mode.`;
  $("done-camera-link").href = `/cameras/${encodeURIComponent(res.camera_id)}`;
  $("done-events").innerHTML = `<table><thead><tr><th>Event</th><th>Timestamp</th><th>Duration</th><th>Trigger</th><th>Clip</th></tr></thead><tbody>${
    res.events.map((e) => `<tr><td class="mono">${esc(e.event_id)}</td><td class="mono">${esc(e.timestamp)}</td>
      <td>${e.duration_s.toFixed(2)} s</td><td>${e.trigger_offset_s.toFixed(2)} s</td><td class="mono small">${esc(e.clip_path)}</td></tr>`).join("")
  }</tbody></table>`;
}

// --- wiring ------------------------------------------------------------------------
function markMode() {
  for (const b of document.querySelectorAll("#ts-mode button")) b.classList.toggle("on", b.dataset.mode === state.tsMode);
}

document.addEventListener("DOMContentLoaded", () => {
  loadCameras().catch((e) => toast(e.message, "err"));
  const dz = $("dropzone");
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files[0]); });
  $("file-input").addEventListener("change", (e) => upload(e.target.files[0]));
  $("camera-id").addEventListener("input", () => { setStart(defaultStart()); });
  $("start-ts").addEventListener("input", refreshAll);
  for (const b of document.querySelectorAll("#ts-mode button")) {
    b.addEventListener("click", () => { state.tsMode = b.dataset.mode; markMode(); setStart(defaultStart()); });
  }
  $("btn-save").addEventListener("click", save);
  $("btn-discard").addEventListener("click", async () => {
    if (state.session) await api(`/api/import/sessions/${state.session.id}`, { method: "DELETE" }).catch(() => {});
    resetToDrop();
  });
  $("btn-another").addEventListener("click", resetToDrop);
});
