// Alerts list with filters.
"use strict";

const STATUSES = ["", "ACTIVE", "NEEDS_REVIEW", "CONFIRMED", "DISMISSED", "SUPERSEDED"];
const filters = { status: "", camera: "", text: "", superseded: false };
let alerts = [];

function card(a) {
  return `<a class="card alert-card" href="/alerts/${a.id}" style="color:inherit;text-decoration:none">
    ${a.key_frame_path ? `<img loading="lazy" src="/api/alerts/${a.id}/key_frame.jpg" alt="">` : `<img alt="">`}
    <div class="stack">
      <div class="row">${badge(a.status)} ${badge(a.kind === "DETECTED" ? "detected" : "needs review", a.kind === "DETECTED" ? "ACTIVE" : "NEEDS_REVIEW")}
        ${badge(a.scope)} <b>${esc(a.title)}</b> <span class="right small muted">#${a.id}</span></div>
      <div class="small muted">“${esc(a.prompt)}”</div>
      <div class="small"><b>${esc(a.camera_id)}</b> · ${fmtTs(a.episode_start_iso)} → ${fmtTs(a.episode_end_iso).slice(11)} · ${a.event_ids.length} event(s)
        · confidence ${a.confidence != null ? a.confidence.toFixed(2) : "–"}</div>
      <div class="small">${esc((a.reasoning || "").slice(0, 220))}</div>
    </div></a>`;
}

function renderList() {
  const text = filters.text.toLowerCase();
  const rows = alerts.filter((a) => (!filters.camera || a.camera_id === filters.camera) &&
    (!text || (a.title + " " + a.prompt).toLowerCase().includes(text)));
  document.getElementById("list").innerHTML = rows.length ? rows.map(card).join("") : `<div class="card empty">No alerts match the filters.</div>`;
}

async function load() {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status", filters.status);
  if (filters.superseded) qs.set("include_superseded", "true");
  alerts = (await api(`/api/alerts?${qs}`)).alerts;
  const cams = [...new Set(alerts.map((a) => a.camera_id))].sort();
  const sel = document.getElementById("f-camera");
  const keep = filters.camera;
  sel.innerHTML = `<option value="">all cameras</option>` + cams.map((c) => `<option ${c === keep ? "selected" : ""}>${esc(c)}</option>`).join("");
  renderList();
}

document.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(location.search);
  filters.status = params.get("status") || "";
  document.getElementById("app").innerHTML = `
    <div class="page-head"><div><h1>Alerts</h1><p>Alerts are kept per prompt and episode; re-runs supersede older ones.</p></div></div>
    <div class="card row">
      <label class="field">Status<select id="f-status">${STATUSES.map((s) => `<option value="${s}" ${s === filters.status ? "selected" : ""}>${s || "all (current)"}</option>`).join("")}</select></label>
      <label class="field">Camera<select id="f-camera"></select></label>
      <label class="field" style="flex:1">Prompt contains<input type="text" id="f-text" placeholder="e.g. package"></label>
      <label class="switch" style="align-self:end;margin-bottom:6px"><input type="checkbox" id="f-sup"> include superseded</label>
    </div>
    <div id="list" class="stack" style="margin-top:16px"></div>`;
  document.getElementById("f-status").addEventListener("change", (e) => { filters.status = e.target.value; load(); });
  document.getElementById("f-camera").addEventListener("change", (e) => { filters.camera = e.target.value; renderList(); });
  document.getElementById("f-text").addEventListener("input", (e) => { filters.text = e.target.value; renderList(); });
  document.getElementById("f-sup").addEventListener("change", (e) => { filters.superseded = e.target.checked; load(); });
  load().catch((e) => toast(e.message, "err"));
  let t = null;
  onStream((msg) => { if (msg.type === "alert") { clearTimeout(t); t = setTimeout(load, 500); } });
});
