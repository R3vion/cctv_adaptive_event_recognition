// Camera page: prompts (create / edit / versions / backfill), episode timeline,
// event details (media, frame selection, observations, decisions with history, re-run, debug).
"use strict";

const cameraId = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
const st = { data: null, open: new Set(), details: {}, media: {}, players: {}, form: null };

const ICON = { met: "✔", not_met: "✘", unknown: "?" };

function decisionChip(cond, res) {
  if (!res) return `<span class="badge">${esc(cond.title)}: –</span>`;
  const conf = res.confidence != null ? ` ${res.confidence.toFixed(2)}` : "";
  return `<span class="badge b-${esc(res.decision)}" title="${esc(cond.prompt)}">${esc(cond.title)}: ${esc(res.decision)}${conf}</span>`;
}

// --- loading & top-level render ---------------------------------------------------------
async function load() {
  st.data = await api(`/api/cameras/${encodeURIComponent(cameraId)}`);
  render();
  for (const id of st.open) loadDetail(id, false);
}

function render() {
  const d = st.data;
  Object.keys(st.players).forEach(stopPlayer); // the detail boxes are rebuilt below
  document.title = `${d.camera_id} · CCTV Analytics`;
  const allEvents = d.episodes.flatMap((e) => e.events).concat(d.unassigned);
  const waiting = allEvents.filter((e) => ["WAITING", "ERROR", "NEW"].includes(e.queue_status)).length;
  document.getElementById("app").innerHTML = `
    <div class="page-head">
      <div><h1>${esc(d.camera_id)}</h1>
        <p>${allEvents.length} events · ${d.episodes.length} episodes · ${d.sources.length} video(s): ${d.sources.map((s) => esc(s.filename)).join(", ") || "–"}</p></div>
      <div class="row">
        ${waiting ? `<button class="btn primary" id="btn-analyze">Analyze ${waiting} waiting / failed event(s)</button>` : ""}
        <a class="btn" href="/import">Import video</a>
        <button class="btn danger" id="btn-delete-camera">Delete camera</button>
      </div>
    </div>
    <div class="card" id="prompts"></div>
    <div class="card" id="pending-events" ${d.unassigned.length ? "" : "hidden"}></div>
    <div style="margin-top:16px" id="episodes"></div>`;
  const btn = document.getElementById("btn-analyze");
  if (btn) btn.addEventListener("click", async () => {
    const r = await api(`/api/cameras/${encodeURIComponent(cameraId)}/analyze`, { method: "POST" });
    toast(`${r.queued} event(s) queued`, "ok");
    load();
  });
  document.getElementById("btn-delete-camera").addEventListener("click", async () => {
    const cam = { camera_id: d.camera_id, events: allEvents.length, sources: d.sources.length, prompts: d.conditions.length };
    if (await deleteCamera(cam)) location.href = "/cameras";
  });
  renderPrompts();
  renderEpisodes();
}

// --- prompts ----------------------------------------------------------------------------
function renderPrompts() {
  const d = st.data;
  const box = document.getElementById("prompts");
  const items = d.conditions.map((c) => `
    <div class="stack" style="padding:10px 0;border-bottom:1px solid var(--border)" data-cid="${c.id}">
      <div class="row">
        <b>${esc(c.title)}</b> ${badge(c.scope)} <span class="muted small">v${c.version}${c.scope_overridden ? " · scope set by user" : ""}</span>
        <div class="row right">
          <label class="switch small"><input type="checkbox" data-act="toggle" ${c.enabled ? "checked" : ""}> enabled</label>
          <button class="btn small" data-act="edit">Edit</button>
          <button class="btn small" data-act="backfill" title="Evaluate this prompt on every already analysed episode">Run on existing events</button>
          <button class="btn small" data-act="versions">Versions</button>
          <button class="btn small danger" data-act="delete">Delete</button>
        </div>
      </div>
      <div class="muted">“${esc(c.prompt)}”</div>
      <details><summary class="small">Focus questions & decision criteria</summary>
        <ol class="small" style="margin:4px 0 6px;padding-left:20px">${c.focus_questions.map((q) => `<li><b>${esc(q.id)}</b> ${esc(q.question)}</li>`).join("")}</ol>
        <div class="small"><b>Criteria:</b> ${esc(c.decision_criteria)}</div>
        ${c.suggestion ? `<div class="small muted" style="margin-top:4px">LLM suggested scope: ${esc(c.suggestion.scope)} – ${esc(c.suggestion.scope_rationale || "")}</div>` : ""}
      </details>
      <div class="versions small"></div>
    </div>`).join("");
  box.innerHTML = `
    <div class="row spread"><h2 style="margin:0">Prompts</h2><button class="btn primary" id="btn-add">Add prompt</button></div>
    <div id="prompt-form"></div>
    ${items || `<div class="empty">No prompts yet. Add one to tell the system what to alert on.</div>`}`;
  document.getElementById("btn-add").addEventListener("click", () => openForm(null));
  box.querySelectorAll("[data-cid]").forEach((el) => {
    const cond = d.conditions.find((c) => c.id === Number(el.dataset.cid));
    el.querySelector("[data-act=toggle]").addEventListener("change", (e) => patchCondition(cond, e.target.checked));
    el.querySelector("[data-act=edit]").addEventListener("click", () => openForm(cond));
    el.querySelector("[data-act=backfill]").addEventListener("click", () => backfill(cond));
    el.querySelector("[data-act=versions]").addEventListener("click", () => showVersions(cond, el.querySelector(".versions")));
    el.querySelector("[data-act=delete]").addEventListener("click", () => deleteCondition(cond));
  });
  if (st.form) renderForm();
}

async function patchCondition(cond, enabled) {
  try { await api(`/api/conditions/${cond.id}`, { method: "PATCH", body: { enabled } }); toast(`${cond.title}: ${enabled ? "enabled" : "disabled"}`, "ok"); }
  catch (e) { toast(e.message, "err"); }
  load();
}
async function backfill(cond) {
  try { const r = await api(`/api/conditions/${cond.id}/backfill`, { method: "POST", body: {} }); toast(`Queued as job #${r.job_id}`, "ok"); }
  catch (e) { toast(e.message, "err"); }
}
async function deleteCondition(cond) {
  if (!confirm(`Delete the prompt “${cond.title}”? Its results stay in the history.`)) return;
  await api(`/api/conditions/${cond.id}`, { method: "DELETE" });
  load();
}
async function showVersions(cond, box) {
  if (box.innerHTML) { box.innerHTML = ""; return; }
  const r = await api(`/api/conditions/${cond.id}/versions`);
  box.innerHTML = `<table><thead><tr><th>Version</th><th>Created</th><th>Scope</th><th>Prompt</th></tr></thead><tbody>${
    r.versions.map((v) => `<tr><td>v${v.version}</td><td class="mono">${fmtTs(v.created_at)}</td><td>${badge(v.scope)}</td><td>${esc(v.prompt)}</td></tr>`).join("")}</tbody></table>`;
}

// add / edit form
// "Run on existing events" follows the dashboard's auto mode until the user ticks it by hand.
function autoBackfill() {
  return st.data.analysis_mode === "auto";
}

function openForm(cond) {
  st.form = cond
    ? { id: cond.id, prompt: cond.prompt, title: cond.title, scope: cond.scope, questions: cond.focus_questions.map((q) => q.question),
        criteria: cond.decision_criteria, suggestion: cond.suggestion, llm_call_id: cond.llm_call_id, analysed: true, busy: false }
    : { id: null, prompt: "", analysed: false, busy: false };
  Object.assign(st.form, { backfill: autoBackfill(), backfillTouched: false });
  renderForm();
  document.getElementById("prompt-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderForm() {
  const f = st.form;
  const box = document.getElementById("prompt-form");
  if (!f) { box.innerHTML = ""; return; }
  const sugg = f.suggestion;
  box.innerHTML = `
    <div class="card" style="margin:12px 0;background:var(--surface-2)">
      <h3>${f.id ? "Edit prompt (saves a new version)" : "New prompt"}</h3>
      <label class="field">Alert me when…
        <textarea id="f-prompt" placeholder="e.g. Alert me if someone leaves a package at the door and then walks away.">${esc(f.prompt)}</textarea></label>
      <div class="row" style="margin-top:8px">
        <button class="btn" id="f-analyze" ${f.busy ? "disabled" : ""}>${f.busy ? "Analysing with the LLM…" : f.analysed ? "Re-analyse with the LLM" : "Analyse with the LLM"}</button>
        <span class="muted small">The LLM proposes the scope, focus questions and decision criteria – you can change all of them.</span>
      </div>
      ${f.analysed ? `
      <div class="grid grid-2" style="margin-top:12px">
        <label class="field">Title<input type="text" id="f-title" value="${esc(f.title)}"></label>
        <label class="field">Scope
          <select id="f-scope">${["frame", "event", "history"].map((s) => `<option ${s === f.scope ? "selected" : ""}>${s}</option>`).join("")}</select>
        </label>
      </div>
      ${sugg ? `<div class="note info small" style="margin-top:8px">LLM suggested <b>${esc(sugg.scope)}</b>: ${esc(sugg.scope_rationale || "")}</div>` : ""}
      <label class="field" style="margin-top:8px">Focus questions (one per line)
        <textarea id="f-questions" rows="4">${esc(f.questions.join("\n"))}</textarea></label>
      <label class="field" style="margin-top:8px">Decision criteria
        <textarea id="f-criteria" rows="3">${esc(f.criteria)}</textarea></label>
      <div class="row spread" style="margin-top:10px">
        <label class="switch" title="Ticked by default while “Analyse new events automatically” is on (dashboard)">
          <input type="checkbox" id="f-backfill" ${f.backfill ? "checked" : ""}> Run on existing events after saving</label>
        <div class="row"><button class="btn ghost" id="f-cancel">Cancel</button><button class="btn primary" id="f-save">Save</button></div>
      </div>` : `<div class="row" style="margin-top:8px"><button class="btn ghost" id="f-cancel">Cancel</button></div>`}
    </div>`;
  const val = (id) => document.getElementById(id);
  val("f-prompt").addEventListener("input", (e) => { f.prompt = e.target.value; });
  val("f-cancel").addEventListener("click", () => { st.form = null; renderForm(); });
  val("f-analyze").addEventListener("click", async () => {
    f.prompt = val("f-prompt").value;
    f.busy = true; renderForm();
    try {
      const s = await api("/api/conditions/analyze", { method: "POST", body: { prompt: f.prompt } });
      Object.assign(f, { title: s.title, scope: s.scope, questions: s.focus_questions.map((q) => q.question),
        criteria: s.decision_criteria, suggestion: s.suggestion, llm_call_id: s.llm_call_id, analysed: true });
    } catch (e) { toast(e.message, "err", 8000); }
    f.busy = false; renderForm();
  });
  if (f.analysed) {
    val("f-backfill").addEventListener("change", (e) => { f.backfill = e.target.checked; f.backfillTouched = true; });
    val("f-save").addEventListener("click", async () => {
      const body = {
        prompt: val("f-prompt").value, title: val("f-title").value, scope: val("f-scope").value,
        focus_questions: val("f-questions").value.split("\n").map((s) => s.trim()).filter(Boolean),
        decision_criteria: val("f-criteria").value, suggestion: f.suggestion, llm_call_id: f.llm_call_id,
        backfill: val("f-backfill").checked,
      };
      try {
        if (f.id) await api(`/api/conditions/${f.id}`, { method: "PUT", body });
        else await api(`/api/cameras/${encodeURIComponent(cameraId)}/conditions`, { method: "POST", body });
        toast("Prompt saved", "ok");
        st.form = null;
        load();
      } catch (e) { toast(e.message, "err", 8000); }
    });
  }
}

// --- episodes & events --------------------------------------------------------------------
function eventRow(ev) {
  const conds = st.data.conditions;
  return `
    <div class="event-row" data-eid="${esc(ev.event_id)}" id="ev-${esc(ev.event_id.replace("#", "-"))}">
      <img class="thumb" loading="lazy" src="/api/events/${encodeURIComponent(ev.event_id)}/trigger.jpg" alt="">
      <div style="cursor:pointer" data-act="toggle">
        <div class="row"><b class="mono">${esc(ev.event_id)}</b> <span class="mono small muted">${esc(ev.timestamp)}</span>
          <span class="small muted">${ev.duration_s.toFixed(1)} s</span> ${badge(ev.queue_status)}
          ${ev.error ? `<span class="small" style="color:var(--danger)">${esc(ev.error.slice(0, 120))}</span>` : ""}
          <span class="right small muted">${st.open.has(ev.event_id) ? "▲ details" : "▼ details"}</span></div>
        <div class="cond-cells">${conds.map((c) => decisionChip(c, ev.results[c.id])).join("")}</div>
      </div>
      <div class="event-detail ${st.open.has(ev.event_id) ? "" : "hidden"}"></div>
    </div>`;
}

function renderEpisodes() {
  const d = st.data;
  const condTitle = (id) => (d.conditions.find((c) => c.id === Number(id)) || { title: `prompt ${id}` }).title;
  const pend = document.getElementById("pending-events");
  if (d.unassigned.length) {
    pend.innerHTML = `<h2>Not analysed yet</h2><div class="episode">${d.unassigned.map(eventRow).join("")}</div>`;
  }
  document.getElementById("episodes").innerHTML = d.episodes.length ? d.episodes.map((ep) => `
    <div class="episode">
      <div class="episode-head stack">
        <div class="row">
          <b>Episode #${ep.id}</b> ${badge(ep.status)}
          <span class="mono small">${fmtTs(ep.start_iso)} → ${fmtTs(ep.end_iso).slice(11)}</span>
          <span class="small muted">${ep.event_count} event(s)${ep.close_reason ? " · closed: " + esc(ep.close_reason) : ""}</span>
          <span class="right row">${ep.alerts.map((a) => `<a href="/alerts/${a.id}">${badge(`${a.kind === "DETECTED" ? "ALERT" : "REVIEW"} #${a.id} ${a.title}`, a.status)}</a>`).join(" ")}</span>
        </div>
        ${Object.keys(ep.closing).length ? `<div class="cond-cells">${Object.entries(ep.closing).map(([cid, r]) =>
          `<span class="badge b-${esc(r.decision)}">closing · ${esc(condTitle(cid))}: ${esc(r.decision)}${r.confidence != null ? " " + r.confidence.toFixed(2) : ""}</span>`).join("")}</div>` : ""}
        ${ep.summary ? `<div class="small">${esc(ep.summary)}</div>` : ""}
      </div>
      ${ep.events.map(eventRow).join("")}
    </div>`).join("") : `<div class="card empty">No analysed events yet.</div>`;
  document.querySelectorAll(".event-row [data-act=toggle]").forEach((el) => {
    el.addEventListener("click", () => toggleEvent(el.closest(".event-row").dataset.eid));
  });
}

function toggleEvent(id) {
  if (st.open.has(id)) { st.open.delete(id); stopPlayer(id); } else st.open.add(id);
  const row = document.querySelector(`.event-row[data-eid="${CSS.escape(id)}"]`);
  const box = row.querySelector(".event-detail");
  box.classList.toggle("hidden", !st.open.has(id));
  row.querySelector(".right.small").textContent = st.open.has(id) ? "▲ details" : "▼ details";
  if (st.open.has(id)) loadDetail(id, true);
}

async function loadDetail(id, showLoading) {
  const row = document.querySelector(`.event-row[data-eid="${CSS.escape(id)}"]`);
  if (!row) return;
  const box = row.querySelector(".event-detail");
  box.classList.remove("hidden");
  if (showLoading && !st.details[id]) box.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    st.details[id] = await api(`/api/events/${encodeURIComponent(id)}`);
    renderDetail(box, st.details[id]);
  } catch (e) { box.innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
}

function stopPlayer(id) {
  if (st.players[id]) st.players[id].stop();
  delete st.players[id];
}

function frameUrl(eventId, t, w) {
  return `/api/events/${encodeURIComponent(eventId)}/frame?t=${t.toFixed(3)}&w=${w}`;
}

function renderDetail(box, det) {
  const ev = det.event;
  const id = ev.event_id;
  const mode = st.media[id] || "frames";
  const conds = st.data.conditions;
  const obs = det.observations[0];
  const shown = obs ? obs.frames : (det.selection ? det.selection.frames.map((f, i) => ({ ...f, n: i + 1 })) : []);
  box.innerHTML = `
    <div class="grid grid-2" style="margin-top:10px">
      <div class="stack">
        <div class="row spread">
          <div class="seg"><button data-media="frames" class="${mode === "frames" ? "on" : ""}">Frames the model saw</button>
            <button data-media="video" class="${mode === "video" ? "on" : ""}">Video (MP4 preview)</button></div>
          <span class="small muted">${shown.length} frame(s)${obs ? ` · observation #${obs.id}` : ""}</span>
        </div>
        <div class="media">${mode === "video"
          ? `<video controls muted playsinline preload="metadata" style="width:100%;border-radius:8px;background:#000" src="/api/events/${encodeURIComponent(id)}/preview.mp4"></video>
             <div class="small muted">The 10 s MJPEG clip converted to MP4 for the browser (no audio).</div>`
          : shown.length ? `<div class="kf-holder"></div>` : `<div class="empty">No frames selected yet.</div>`}</div>
      </div>
      <div class="stack">
        <h3 style="margin:0">Frame selection <span class="small muted">${det.selection ? `${esc(det.selection.origin)} · threshold ${det.selection.threshold.toFixed(2)}% (${esc(det.selection.threshold_source)}) · ${det.selection.stats.candidates} candidates → ${det.selection.stats.selected} frames` : "not computed yet"}</span></h3>
        <div class="sel-chart"></div>
        <div class="filmstrip sel-film"></div>
      </div>
    </div>
    <div class="stack" style="margin-top:12px">${conds.map((c) => conditionBlock(c, det)).join("")}</div>
    ${det.observations.length ? `<details style="margin-top:10px"><summary>Observations (${det.observations.length})</summary>${det.observations.map((o) => `
      <div class="stack" style="padding:6px 0;border-bottom:1px dashed var(--border)">
        <div class="small muted">#${o.id} · ${fmtTs(o.created_at)} · ${o.frames.length} frames · covers prompt version(s) ${o.covered_versions.join(", ")}
          ${o.llm_call_id ? ` · <a href="#" data-call="${o.llm_call_id}">LLM call #${o.llm_call_id}</a>` : ""}</div>
        <div>${esc(o.description)}</div>
        <ul class="small" style="margin:0;padding-left:18px">${o.focus_answers.map((a) => `<li><b>${esc(a.id)}</b> ${esc(a.answer)} – ${esc(a.evidence)}</li>`).join("")}</ul>
      </div>`).join("")}</details>` : ""}`;

  stopPlayer(id);
  const holder = box.querySelector(".kf-holder");
  if (holder) {
    st.players[id] = new KeyframePlayer(holder, { full: (f) => frameUrl(id, f.t, 0) });
    st.players[id].setFrames(shown, (f) => frameUrl(id, f.t, 640));
    st.players[id].start();
  }
  box.querySelectorAll("[data-media]").forEach((b) => b.addEventListener("click", () => { st.media[id] = b.dataset.media; renderDetail(box, det); }));
  box.querySelectorAll("[data-call]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showCall(Number(a.dataset.call)); }));
  box.querySelectorAll("[data-rerun]").forEach((b) => b.addEventListener("click", async () => {
    const force = box.querySelector(`[data-force="${b.dataset.rerun}"]`).checked;
    try {
      const r = await api(`/api/events/${encodeURIComponent(id)}/rerun`, { method: "POST", body: { condition_id: Number(b.dataset.rerun), force_observe: force } });
      toast(`Re-run queued (job #${r.job_id})`, "ok");
    } catch (e) { toast(e.message, "err"); }
  }));
  if (det.selection) {
    const chart = new SelectionChart(box.querySelector(".sel-chart"), { editable: false, thumb: (i, t) => frameUrl(id, t, 200) });
    chart.update({ ...det.selection, chart: det.selection.signals, threshold_source: det.selection.threshold_source });
    renderFilmstrip(box.querySelector(".sel-film"), det.selection.frames, (i, t) => frameUrl(id, t, 200));
  }
}

function conditionBlock(cond, det) {
  const evals = det.evaluations.filter((e) => e.condition_id === cond.id && !e.is_closing);
  const cur = evals.find((e) => !e.superseded);
  const results = cur && cur.focus_results ? `<ul class="focus-list">${cur.focus_results.map((r) =>
    `<li><span class="focus-icon ${esc(r.status)}">${ICON[r.status] || "?"}</span><span><b>${esc(r.id)}</b> ${esc(r.question)}<br><span class="small muted">${esc(r.evidence)}</span></span></li>`).join("")}</ul>` : "";
  const history = evals.length ? `<details><summary class="small">Run history (${evals.length})</summary><table class="small"><thead><tr>
      <th>When</th><th>Run</th><th>Version</th><th>Decision</th><th>Confidence</th><th>Debug</th></tr></thead><tbody>${evals.map((e) => `
      <tr style="${e.superseded ? "opacity:.6" : ""}"><td class="mono">${fmtTs(e.created_at)}</td><td>${esc(e.run ? e.run.kind : "")} #${e.run_id}</td>
        <td>v${e.condition_version_id}</td><td>${badge(e.decision)}${e.superseded ? " superseded" : ""}</td>
        <td>${e.confidence != null ? e.confidence.toFixed(2) : "–"}</td>
        <td>${e.llm_call_id ? `<a href="#" data-call="${e.llm_call_id}">call #${e.llm_call_id}</a>` : ""}</td></tr>`).join("")}</tbody></table></details>` : "";
  return `
    <div class="card" style="padding:12px">
      <div class="row">
        <b>${esc(cond.title)}</b> ${badge(cond.scope)}
        ${cur ? `${badge(cur.decision)} <span class="small muted">confidence ${cur.confidence != null ? cur.confidence.toFixed(2) : "–"} · ${esc(cur.run ? cur.run.kind : "")}</span>` : `<span class="muted small">not evaluated</span>`}
        <span class="right row">
          <label class="switch small"><input type="checkbox" data-force="${cond.id}"> force re-observe</label>
          <button class="btn small" data-rerun="${cond.id}" title="${cond.scope === "history" ? "Replays the whole episode" : "Re-evaluates this event only"}">Re-run${cond.scope === "history" ? " episode" : ""}</button>
        </span>
      </div>
      ${cur && cur.reasoning ? `<div style="margin-top:6px">${esc(cur.reasoning)}</div>` : ""}
      ${results}
      ${history}
    </div>`;
}

// --- LLM call viewer ------------------------------------------------------------------------
async function showCall(callId) {
  const c = await api(`/api/llm-calls/${callId}`);
  let dlg = document.getElementById("call-dialog");
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "call-dialog";
    dlg.style.cssText = "max-width:min(980px,94vw);width:100%;border:1px solid var(--border);border-radius:12px;background:var(--surface);color:var(--text);padding:16px";
    document.body.appendChild(dlg);
  }
  const imgs = (c.images || []).map((i) => `${esc(i.label)} – sent ${i.sent.join("×")} (original ${i.original.join("×")})`).join("\n");
  dlg.innerHTML = `
    <div class="row spread"><h2 style="margin:0">LLM call #${c.id} · ${esc(c.purpose)}</h2><button class="btn" id="dlg-close">Close</button></div>
    <div class="small muted" style="margin:6px 0">${esc(c.model)} · thinking ${c.think ? "on" : "off"} · ${(c.duration_ms / 1000).toFixed(1)} s · prompt ${c.prompt_tokens ?? "?"} tokens · output ${c.output_tokens ?? "?"} tokens · attempts ${c.attempts}${c.error ? ` · <span style="color:var(--danger)">${esc(c.error)}</span>` : ""}</div>
    <details open><summary>System prompt</summary><pre>${esc(c.system)}</pre></details>
    <details open><summary>User prompt</summary><pre>${esc(c.prompt)}</pre></details>
    ${imgs ? `<details><summary>Images (${c.images.length})</summary><pre>${imgs}</pre></details>` : ""}
    ${c.thinking ? `<details><summary>Thinking</summary><pre>${esc(c.thinking)}</pre></details>` : ""}
    <details open><summary>Raw response</summary><pre>${esc(c.response || "")}</pre></details>`;
  dlg.querySelector("#dlg-close").addEventListener("click", () => dlg.close());
  dlg.showModal();
}

// --- live updates -----------------------------------------------------------------------------
let reloadTimer = null;
document.addEventListener("DOMContentLoaded", () => {
  load().catch((e) => { document.getElementById("app").innerHTML = `<div class="card note err">${esc(e.message)}</div>`; });
  onStream((msg) => {
    if (msg.type === "settings" && st.data && "analysis_mode" in msg.values) {
      st.data.analysis_mode = msg.values.analysis_mode;
      const f = st.form, box = document.getElementById("f-backfill");
      if (f && !f.backfillTouched) {
        f.backfill = autoBackfill();
        if (box) box.checked = f.backfill; // no re-render: it would drop unsaved edits in the form
      }
      return;
    }
    const mine = msg.camera_id === cameraId || (msg.event_id && msg.event_id.startsWith(cameraId + "#")) || msg.type === "job";
    if (!mine || st.form) return;
    if (["evaluation", "queue", "alert", "episode", "conditions", "job"].includes(msg.type)) {
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => load().catch(() => {}), 700);
    }
  });
});
