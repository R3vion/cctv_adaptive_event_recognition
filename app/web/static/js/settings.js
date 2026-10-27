// Settings: every editable setting (overrides .env), Ollama connection test, system prompts.
"use strict";

let items = [];

function input(s) {
  const id = `s-${s.key}`;
  const dis = s.editable ? "" : "disabled";
  if (s.type === "bool") return `<label class="switch"><input type="checkbox" id="${id}" ${s.value ? "checked" : ""} ${dis}> ${s.value ? "on" : "off"}</label>`;
  if (s.choices) return `<select id="${id}" ${dis}>${s.choices.map((c) => `<option ${c === s.value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  const type = s.type === "int" || s.type === "float" ? "number" : "text";
  const step = s.type === "float" ? "any" : "1";
  return `<input type="${type}" id="${id}" value="${esc(s.value)}" ${type === "number" ? `step="${step}"` : ""}
    ${s.min != null ? `min="${s.min}"` : ""} ${s.max != null ? `max="${s.max}"` : ""} ${dis} style="width:100%">`;
}

function read(s) {
  const el = document.getElementById(`s-${s.key}`);
  if (s.type === "bool") return el.checked;
  if (s.type === "int") return parseInt(el.value, 10);
  if (s.type === "float") return parseFloat(el.value);
  return el.value;
}

function render() {
  const groups = [...new Set(items.map((s) => s.group))];
  document.getElementById("groups").innerHTML = groups.map((g) => `
    <div class="card">
      <h2>${esc(g)}</h2>
      <table><tbody>${items.filter((s) => s.group === g).map((s) => `
        <tr><td style="width:34%"><b>${esc(s.label)}</b><div class="small muted mono">${esc(s.key.toUpperCase())}</div>
            ${s.help ? `<div class="small muted">${esc(s.help)}</div>` : ""}</td>
          <td>${input(s)}</td>
          <td style="width:200px" class="small muted">${s.editable ? (s.overridden
            ? `${badge("overridden", "PENDING")} .env: <span class="mono">${esc(s.env_value)}</span> <button class="btn small ghost" data-reset="${s.key}">reset</button>`
            : `from .env`) : "environment only"}</td></tr>`).join("")}</tbody></table>
    </div>`).join("");
  document.querySelectorAll("[data-reset]").forEach((b) => b.addEventListener("click", async () => {
    items = (await api(`/api/settings/${b.dataset.reset}`, { method: "DELETE" })).settings;
    render();
    toast("Reset to the .env value", "ok");
  }));
}

async function save() {
  const values = {};
  for (const s of items.filter((s) => s.editable)) {
    const v = read(s);
    if (JSON.stringify(v) !== JSON.stringify(s.value)) values[s.key] = v;
  }
  if (!Object.keys(values).length) { toast("Nothing changed"); return; }
  try {
    items = (await api("/api/settings", { method: "PUT", body: { values } })).settings;
    render();
    toast(`Saved ${Object.keys(values).length} setting(s)`, "ok");
  } catch (e) { toast(e.message, "err", 8000); }
}

async function testOllama() {
  const out = document.getElementById("ollama-result");
  out.className = "note"; out.textContent = "Testing…";
  const r = await api("/api/settings/test-ollama", { method: "POST" });
  out.className = `note ${r.ok && r.model_available ? "ok" : r.ok ? "warn" : "err"}`;
  out.textContent = r.ok
    ? `Reachable – Ollama ${r.version} at ${r.url}. Model ${r.model} ${r.model_available ? "is available" : "is NOT pulled on this server"}.`
    : `Not reachable at ${r.url}: ${r.error}`;
  refreshStatus();
}

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("app").innerHTML = `
    <div class="page-head">
      <div><h1>Settings</h1><p>Values saved here override the <span class="mono">.env</span> file and take effect immediately.</p></div>
      <div class="row"><button class="btn" id="btn-test">Test Ollama connection</button><button class="btn primary" id="btn-save">Save changes</button></div>
    </div>
    <div id="ollama-result" class="note hidden"></div>
    <div id="groups" class="stack" style="margin-top:16px"></div>
    <div class="card" style="margin-top:16px"><h2>System prompts</h2>
      <p class="muted small">Read-only templates from <span class="mono">app/prompts/*.md</span>. Every call stores the filled-in prompt and the raw answer (see the debug links on the camera page).</p>
      <div id="prompts"></div></div>`;
  document.getElementById("btn-save").addEventListener("click", save);
  document.getElementById("btn-test").addEventListener("click", () => { document.getElementById("ollama-result").classList.remove("hidden"); testOllama(); });
  try {
    items = (await api("/api/settings")).settings;
    render();
    const t = (await api("/api/prompts")).templates;
    document.getElementById("prompts").innerHTML = t.map((p) => `<details><summary class="mono">${esc(p.name)}.md</summary>
      <div class="small muted">System</div><pre>${esc(p.system)}</pre><div class="small muted">User template</div><pre>${esc(p.user)}</pre></details>`).join("");
  } catch (e) { toast(e.message, "err"); }
});
