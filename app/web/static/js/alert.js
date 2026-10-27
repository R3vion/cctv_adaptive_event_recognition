// Alert detail: key frame, what triggered it, focus questions, reasoning, episode, review.
"use strict";

const alertId = Number(location.pathname.split("/").filter(Boolean).pop());
const ICONS = { met: "✔", not_met: "✘", unknown: "?" };

async function load() {
  const d = await api(`/api/alerts/${alertId}`);
  const a = d.alert, ep = d.episode;
  document.title = `Alert #${a.id} · CCTV Analytics`;
  const duration = ep ? (new Date(ep.end_iso) - new Date(ep.start_iso)) / 1000 : null;
  const reviewable = ["ACTIVE", "NEEDS_REVIEW"].includes(a.status);
  document.getElementById("app").innerHTML = `
    <div class="page-head">
      <div><h1>${esc(a.title)}</h1>
        <p>${badge(a.status)} ${badge(a.kind === "DETECTED" ? "condition met" : "not enough information – needs review", a.kind === "DETECTED" ? "ACTIVE" : "NEEDS_REVIEW")}
          ${badge(a.scope)} · alert #${a.id} · created ${fmtTs(a.created_at)}</p></div>
      <a class="btn" href="/alerts">All alerts</a>
    </div>
    <div class="grid" style="grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)" id="cols">
      <div class="stack">
        <div class="card">
          ${a.key_frame_path ? `<a href="/api/alerts/${a.id}/key_frame.jpg" target="_blank"><img class="keyframe-big" src="/api/alerts/${a.id}/key_frame.jpg" alt="key frame"></a>` : `<div class="empty">No key frame</div>`}
          <div class="small muted" style="margin-top:6px">Key frame chosen by the model: ${esc(a.key_event_id || "–")}${a.key_frame_t != null ? ` at ${a.key_frame_t.toFixed(2)} s` : ""}</div>
        </div>
        <div class="card">
          <h2>Focus questions</h2>
          <ul class="focus-list">${(a.focus_results || []).map((r) => `<li><span class="focus-icon ${esc(r.status)}">${ICONS[r.status] || "?"}</span>
            <span><b>${esc(r.id)}</b> ${esc(r.question)}<br><span class="small muted">${esc(r.evidence)}</span></span></li>`).join("")}</ul>
        </div>
      </div>
      <div class="stack">
        <div class="card stack">
          <h2 style="margin:0">What triggered it</h2>
          <div>“${esc(a.prompt)}”</div>
          <dl class="kv"><dt>Camera</dt><dd><a href="/cameras/${encodeURIComponent(a.camera_id)}">${esc(a.camera_id)}</a></dd>
            <dt>Decision</dt><dd>${esc(a.decision)} · confidence ${a.confidence != null ? a.confidence.toFixed(2) : "–"}</dd>
            <dt>Prompt version</dt><dd>#${a.condition_version_id}</dd>
            <dt>Episode</dt><dd>#${a.episode_id} · ${fmtTs(a.episode_start_iso)} → ${fmtTs(a.episode_end_iso).slice(11)}${duration != null ? ` (${fmtDuration(duration)})` : ""}</dd></dl>
          <div><b>Reasoning</b><div>${esc(a.reasoning || "–")}</div></div>
          <div><b>Episode summary</b><div class="${a.episode_summary ? "" : "muted"}">${esc(a.episode_summary || (ep && ep.status === "OPEN" ? "The episode is still open – the summary is written when it closes." : "–"))}</div></div>
        </div>
        <div class="card stack">
          <h2 style="margin:0">Review</h2>
          ${a.review_note ? `<div class="note">${esc(a.review_note)}</div>` : ""}
          ${reviewable ? `<textarea id="note" placeholder="Optional note"></textarea>
            <div class="row"><button class="btn primary" data-act="confirm">Confirm</button><button class="btn danger" data-act="dismiss">Dismiss</button></div>`
            : `<div class="muted small">Status: ${esc(a.status)}.</div>`}
        </div>
        <div class="card"><h2>Events (${d.events.length})</h2>
          <div class="stack">${d.events.map((ev) => `
            <a class="row" href="/cameras/${encodeURIComponent(ev.camera_id)}#ev-${esc(ev.event_id.replace("#", "-"))}" style="color:inherit">
              <img src="/api/events/${encodeURIComponent(ev.event_id)}/trigger.jpg" style="width:96px;border-radius:6px;aspect-ratio:16/9;object-fit:cover;background:#000" alt="">
              <span><b class="mono">${esc(ev.event_id)}</b>${ev.event_id === a.key_event_id ? " " + badge("key event", "ACTIVE") : ""}<br><span class="small muted mono">${esc(ev.timestamp)}</span></span></a>`).join("")}</div>
        </div>
      </div>
    </div>`;
  if (innerWidth < 900) document.getElementById("cols").style.gridTemplateColumns = "1fr";
  document.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await api(`/api/alerts/${alertId}/review`, { method: "POST", body: { action: b.dataset.act, note: document.getElementById("note").value || null } });
      toast(`Alert ${b.dataset.act === "confirm" ? "confirmed" : "dismissed"}`, "ok");
      load();
    } catch (e) { toast(e.message, "err"); }
  }));
}

document.addEventListener("DOMContentLoaded", () => {
  load().catch((e) => { document.getElementById("app").innerHTML = `<div class="card note err">${esc(e.message)}</div>`; });
  onStream((msg) => { if (msg.type === "alert" && msg.alert_id === alertId) load(); });
});
