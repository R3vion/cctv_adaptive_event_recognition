// Shared helpers for every page: top bar, API calls, toasts, live stream.
"use strict";

// Deletes a camera everywhere (events, videos, prompts, analysis, alerts) after a confirmation.
async function deleteCamera(cam) {
  const what = `${cam.events || 0} event(s), ${cam.sources || 0} video(s), ${cam.prompts || 0} prompt(s)`;
  if (!confirm(`Delete camera “${cam.camera_id}”?\n\nThis permanently removes ${what}, and all analysis results and alerts of this camera.`)) return false;
  try {
    await api(`/api/cameras/${encodeURIComponent(cam.camera_id)}`, { method: "DELETE" });
    toast(`Camera ${cam.camera_id} deleted`, "ok");
    return true;
  } catch (e) { toast(e.message, "err", 7000); return false; }
}

const NAV = [
  ["dashboard", "/", "Dashboard"],
  ["import", "/import", "Import"],
  ["cameras", "/cameras", "Cameras"],
  ["alerts", "/alerts", "Alerts"],
  ["settings", "/settings", "Settings"],
];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function badge(text, cls) {
  return `<span class="badge b-${esc(cls ?? text)}">${esc(text)}</span>`;
}

function fmtTs(iso) {
  if (!iso) return "–";
  return iso.replace("T", " ");
}

function fmtDuration(seconds) {
  if (seconds == null) return "–";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function toast(message, kind = "info", ms = 4500) {
  let box = document.getElementById("toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// --- theme -----------------------------------------------------------------
function storedTheme() {
  try { return localStorage.getItem("theme"); } catch { return null; }
}
function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
}
applyTheme(storedTheme());

function toggleTheme() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark" ||
    (!document.documentElement.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("theme", next); } catch { /* private mode */ }
}

// --- top bar -----------------------------------------------------------------
function renderTopbar() {
  const page = document.body.dataset.page;
  const header = document.getElementById("topbar");
  if (!header) return;
  header.className = "topbar";
  header.innerHTML = `
    <div class="topbar-inner">
      <a class="brand" href="/"><span class="brand-dot"></span>CCTV Analytics</a>
      <nav class="nav">${NAV.map(([key, href, label]) =>
        `<a href="${href}" class="${key === page ? "active" : ""}">${label}</a>`).join("")}</nav>
      <div class="status-pills">
        <span class="pill" id="pill-worker" title="Analysis worker"><span class="light"></span><span>worker</span></span>
        <span class="pill" id="pill-ollama" title="Ollama"><span class="light"></span><span>ollama</span></span>
        <button class="theme-toggle" title="Toggle theme" onclick="toggleTheme()">◐</button>
      </div>
    </div>`;
}

function setPill(id, state, text, title) {
  const pill = document.getElementById(id);
  if (!pill) return;
  pill.querySelector(".light").className = `light ${state}`;
  pill.querySelector("span:last-child").textContent = text;
  if (title) pill.title = title;
}

// What is waiting for the worker: camera events and re-run / backfill jobs. Used by the
// dashboard Queue tile and the worker pill. Waiting and errors are events (Analyze retries them).
function queueSummary(queue = {}, jobs = {}) {
  const events = (queue.QUEUED || 0) + (queue.PROCESSING || 0);
  const jobCount = (jobs.QUEUED || 0) + (jobs.RUNNING || 0);
  const waiting = queue.WAITING || 0, errors = queue.ERROR || 0;
  const n = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  return { total: events + jobCount, text: `${n(events, "event")} · ${n(jobCount, "job")} · ${waiting} waiting · ${n(errors, "error")}` };
}

async function refreshStatus() {
  try {
    const st = await api("/api/system/status");
    const o = st.ollama;
    setPill("pill-ollama", o.ok ? (o.model_available ? "ok" : "busy") : "bad",
      o.ok ? (o.model_available ? o.model : `${o.model} missing`) : "ollama offline",
      `Ollama, called by the backend${o.error ? " – " + o.error : ""}`);
    const w = st.worker, q = queueSummary(w.queue, w.jobs);
    setPill("pill-worker", w.busy ? "busy" : "ok",
      w.busy ? `working: ${w.current || "…"}` : q.total ? `${w.mode} · ${q.total} queued` : `${w.mode} · idle`,
      `Analysis worker – ${q.text}`);
  } catch {
    setPill("pill-ollama", "", "ollama ?");
    setPill("pill-worker", "", "worker ?");
  }
}

let statusTimer = null;
function refreshStatusSoon() {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(refreshStatus, 300);
}

// --- live stream (SSE) ------------------------------------------------------------
const streamListeners = [];
let stream = null;
function onStream(handler) {
  streamListeners.push(handler);
  if (!stream) {
    stream = new EventSource("/api/stream");
    stream.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      for (const h of streamListeners) h(msg);
      if (["worker", "queue", "job"].includes(msg.type)) refreshStatusSoon();
    };
  }
}

document.addEventListener("DOMContentLoaded", () => {
  renderTopbar();
  refreshStatus();
  setInterval(refreshStatus, 15000);
});
