// Dashboard: status tiles, live pipeline log, latest alerts.
"use strict";

let dash = null;
let logFilter = "";
const STAGES = ["discover", "episode", "frames", "observe", "decide", "alert", "close", "rerun", "job", "queue", "prompt", "settings", "worker"];

function tile(label, value, sub = "") {
  return `<div class="card stat"><div class="label">${esc(label)}</div><div class="value">${value}</div>${sub ? `<div class="small muted">${sub}</div>` : ""}</div>`;
}

function alertCard(a) {
  const img = a.key_frame_path ? `<img src="/api/alerts/${a.id}/key_frame.jpg" alt="">` : `<img alt="">`;
  return `<a class="card alert-card" href="/alerts/${a.id}" style="color:inherit;text-decoration:none">
    ${img}
    <div class="stack">
      <div class="row">${badge(a.status)} <b>${esc(a.title)}</b></div>
      <div class="small muted">${esc(a.camera_id)} · ${fmtTs(a.episode_start_iso)}</div>
      <div class="small">${esc((a.reasoning || "").slice(0, 160))}</div>
    </div></a>`;
}

function renderShell() {
  document.getElementById("app").innerHTML = `
    <div class="page-head">
      <div><h1>Dashboard</h1><p>Live view of the analysis pipeline: events in, observations and decisions, alerts out.</p></div>
      <div class="row"><a class="btn" href="/import">Import video</a><a class="btn" href="/alerts">All alerts</a></div>
    </div>
    <div class="grid grid-4" id="tiles"></div>
    <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);margin-top:16px" id="dash-cols">
      <div class="card">
        <div class="row spread"><h2 style="margin:0">Pipeline log</h2>
          <div class="row"><select id="log-filter"><option value="">all stages</option>${STAGES.map((s) => `<option>${s}</option>`).join("")}</select></div>
        </div>
        <div class="log" id="log" style="margin-top:10px"></div>
      </div>
      <div class="stack">
        <div class="card" id="worker-card"></div>
        <div class="card"><h2>Latest alerts</h2><div id="latest" class="stack"></div></div>
      </div>
    </div>`;
  document.getElementById("log-filter").addEventListener("change", (e) => { logFilter = e.target.value; loadLog(); });
  if (innerWidth < 900) document.getElementById("dash-cols").style.gridTemplateColumns = "1fr";
}

async function loadStats() {
  dash = await api("/api/dashboard");
  const q = dash.queue, a = dash.alerts;
  document.getElementById("tiles").innerHTML =
    tile("Cameras", dash.cameras, `${dash.events} events`) +
    tile("Active prompts", dash.prompts) +
    tile("Queue", (q.QUEUED || 0) + (q.PROCESSING || 0), `${q.WAITING || 0} waiting · ${q.ERROR || 0} errors · ${q.DONE || 0} done`) +
    tile("Alerts", a.ACTIVE || 0, `${a.NEEDS_REVIEW || 0} need review · ${a.CONFIRMED || 0} confirmed`);
  const w = dash.worker;
  document.getElementById("worker-card").innerHTML = `
    <div class="row spread"><h2 style="margin:0">Worker</h2>${badge(w.busy ? "PROCESSING" : "IDLE", w.busy ? "PROCESSING" : "DONE")}</div>
    <div class="stack" style="margin-top:10px">
      <div class="small muted">${w.busy ? "Working on <b>" + esc(w.current) + "</b>" : "Waiting for new events."}</div>
      <label class="switch"><input type="checkbox" id="auto-mode" ${w.mode === "auto" ? "checked" : ""}> Analyse new events automatically</label>
      <div class="small muted">Manual mode keeps new events WAITING until you press <i>Analyze</i> on the camera page.</div>
    </div>`;
  document.getElementById("auto-mode").addEventListener("change", async (e) => {
    try {
      await api("/api/settings", { method: "PUT", body: { values: { analysis_mode: e.target.checked ? "auto" : "manual" } } });
      toast(`Analysis mode: ${e.target.checked ? "auto" : "manual"}`, "ok");
    } catch (err) { toast(err.message, "err"); }
  });
  document.getElementById("latest").innerHTML = dash.recent_alerts.length
    ? dash.recent_alerts.map(alertCard).join("") : `<div class="empty">No alerts yet.</div>`;
}

function logRow(e) {
  const time = (e.ts || "").slice(11, 19);
  const where = e.event_id
    ? `<a href="/cameras/${encodeURIComponent(e.camera_id || e.event_id.split("#")[0])}">${esc(e.event_id)}</a> · `
    : e.camera_id ? `<a href="/cameras/${encodeURIComponent(e.camera_id)}">${esc(e.camera_id)}</a> · ` : "";
  return `<div class="log-row ${esc(e.level)}"><span class="ts">${time}</span><span>${badge(e.stage, "muted")}</span>
    <span class="msg">${where}${esc(e.message)}</span></div>`;
}

async function loadLog() {
  const data = await api("/api/logs?limit=250");
  const rows = data.entries.filter((e) => !logFilter || e.stage === logFilter);
  document.getElementById("log").innerHTML = rows.length ? rows.map(logRow).join("") : `<div class="empty">Nothing logged yet.</div>`;
}

let statsTimer = null;
function refreshStatsSoon() {
  clearTimeout(statsTimer);
  statsTimer = setTimeout(() => loadStats().catch(() => {}), 400);
}

document.addEventListener("DOMContentLoaded", async () => {
  renderShell();
  try { await Promise.all([loadStats(), loadLog()]); } catch (err) { toast(err.message, "err"); }
  onStream((msg) => {
    if (msg.type === "log") {
      if (logFilter && msg.entry.stage !== logFilter) return;
      const log = document.getElementById("log");
      const empty = log.querySelector(".empty");
      if (empty) empty.remove();
      log.insertAdjacentHTML("afterbegin", logRow(msg.entry));
      while (log.children.length > 400) log.lastElementChild.remove();
    }
    if (["alert", "queue", "worker", "episode"].includes(msg.type)) refreshStatsSoon();
    if (msg.type === "alert" && msg.status === "ACTIVE") toast(`Alert: ${msg.title || "new alert"} (${msg.camera_id})`, "err", 7000);
  });
});
