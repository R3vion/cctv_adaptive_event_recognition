// Cameras list.
"use strict";

async function load() {
  const data = await api("/api/cameras");
  const rows = data.cameras.map((c) => `
    <tr>
      <td><a href="/cameras/${encodeURIComponent(c.camera_id)}"><b>${esc(c.camera_id)}</b></a></td>
      <td>${c.events}</td><td>${c.sources}</td><td>${c.prompts}</td>
      <td>${c.alerts ? badge(String(c.alerts), "ACTIVE") : "0"}</td>
      <td>${c.needs_review ? badge(String(c.needs_review), "NEEDS_REVIEW") : "0"}</td>
      <td class="mono small">${fmtTs(c.last_iso)}</td>
      <td><button class="btn small danger" data-delete="${esc(c.camera_id)}">Delete</button></td>
    </tr>`).join("");
  document.getElementById("app").innerHTML = `
    <div class="page-head">
      <div><h1>Cameras</h1><p>Every camera id that has events or prompts. Open a camera to manage its prompts and follow the analysis.</p></div>
      <a class="btn primary" href="/import">Import video</a>
    </div>
    <div class="card table-wrap">${data.cameras.length ? `
      <table><thead><tr><th>Camera</th><th>Events</th><th>Videos</th><th>Prompts</th><th>Alerts</th><th>Needs review</th><th>Last event</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>` : `<div class="empty">No cameras yet – <a href="/import">import a video</a> first.</div>`}
    </div>`;
  document.querySelectorAll("[data-delete]").forEach((b) => b.addEventListener("click", async () => {
    if (await deleteCamera(data.cameras.find((c) => c.camera_id === b.dataset.delete))) load();
  }));
}

document.addEventListener("DOMContentLoaded", () => {
  load().catch((e) => toast(e.message, "err"));
  let t = null;
  onStream((msg) => {
    if (["alert", "queue", "conditions"].includes(msg.type)) { clearTimeout(t); t = setTimeout(load, 600); }
  });
});
