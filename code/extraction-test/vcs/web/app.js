"use strict";
const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");

/* ---------- helpers ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mmss = (t) => { t = Math.max(0, Math.floor(t)); return String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0"); };
const ago = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

async function api(method, path, body, raw) {
  const opt = { method, headers: { "X-VCS": "1" } };
  if (raw) { opt.body = raw; } else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api/" + path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || r.statusText); e.code = data.code; e.status = r.status; throw e; }
  return data;
}
let toastTimer;
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 4500);
}
function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

/* tiny markdown (escape first, so LLM/OCR text can't inject HTML) */
function md(src) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
  const lines = String(src).replace(/\r/g, "").split("\n"); const out = []; let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^#{1,3}\s/.test(l)) { out.push(`<h1>${inline(l.replace(/^#+\s*/, ""))}</h1>`); i++; }
    else if (/^\*\*[^*]+\*\*\s*$/.test(l)) { out.push(`<div class="sec">${inline(l.replace(/\*\*/g, ""))}</div>`); i++; }
    else if (/^\s*\|/.test(l)) {
      const rows = []; while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
      const [head, ...rest] = body;
      out.push("<table><thead><tr>" + cells(head).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
        rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
    } else if (/^\s*[-*]\s/.test(l)) {
      const items = []; while (i < lines.length && /^\s*[-*]\s/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ""));
      out.push("<ul>" + items.map((x) => `<li>${inline(x)}</li>`).join("") + "</ul>");
    } else if (!l.trim()) { i++; }
    else { const p = []; while (i < lines.length && lines[i].trim() && !/^(#|\s*\||\s*[-*]\s|\*\*[^*]+\*\*\s*$)/.test(lines[i])) p.push(lines[i++]); out.push(`<p>${inline(p.join(" "))}</p>`); }
  }
  return out.join("");
}

/* ---------- theme & connection ---------- */
const root = document.documentElement;
try { const t = localStorage.getItem("vcs-theme"); if (t) root.dataset.theme = t; } catch {}
$("#theme").onclick = () => {
  const cur = root.dataset.theme || "light";
  root.dataset.theme = cur === "dark" ? "light" : "dark";
  try { localStorage.setItem("vcs-theme", root.dataset.theme); } catch {}
};
let status = null;
async function refreshStatus() {
  try { status = await api("GET", "status"); } catch { status = null; }
  const c = $("#conn"), m = status?.meetily;
  const [cls, txt] = !status ? ["bad", "UI server unreachable"] : m.online ? ["ok", "Meetily connected"] : ["bad", "Meetily offline"];
  c.innerHTML = `<span class="dot ${cls}"></span><span>${txt}</span>`;
  return status;
}

/* ---------- jobs ---------- */
function watchJob(jobId, logEl, onDone) {
  const tick = async () => {
    let j; try { j = await api("GET", "jobs/" + jobId); } catch (e) { toast(e.message, true); return; }
    logEl.hidden = false; logEl.innerHTML = j.log.map((l) => `<div>${esc(l)}</div>`).join("") + (j.status === "running" ? "<div class='muted'>working…</div>" : "");
    if (j.status === "running") setTimeout(tick, 700);
    else if (j.status === "failed") { logEl.innerHTML += `<div style="color:hsl(var(--destructive))">${esc(j.error)}</div>`; toast(j.error, true); }
    else onDone && onDone(j);
  };
  tick();
}

/* ---------- pages ---------- */
let navSeq = 0;                       // a page that awaited while the user navigated away must not touch the DOM
const stale = (seq) => seq !== navSeq;
function setActive(r) { document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.r === r)); }

function stat(label, value, dot, sub) {
  return `<div class="card card-pad"><div class="stat-label"><span class="dot ${dot || ""}"></span>${esc(label)}</div>
    <div class="stat-value">${value}</div>${sub ? `<div class="small muted" style="margin-top:4px">${sub}</div>` : ""}</div>`;
}

async function pageHome(seq) {
  setActive("home");
  view.innerHTML = `<div class="page-head"><div><h1>Overview</h1><p>What was said and what was shown, summarised in one place.</p></div>
    <a class="btn primary" href="#/new">New run</a></div><div id="stats" class="grid cols-4"></div>
    <div class="card" id="auto" style="margin-top:16px"></div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h2>Recent runs</h2><a class="btn ghost sm" href="#/runs">View all</a></div><div id="recent"></div></div>`;
  const [s, cap] = await Promise.all([refreshStatus(), api("GET", "capture").catch(() => ({}))]);
  if (stale(seq)) return; const m = s?.meetily || {};
  const rec = m.recording?.state;
  const scopes = (m.whoami?.scopes || []).join(", ");
  $("#stats").innerHTML =
    stat("Meetily Pro", m.online ? "Connected" : "Offline", m.online ? "ok" : "bad",
      m.online ? esc(m.whoami ? `${m.whoami.license_tier} · v${m.whoami.server_version}` : "Automation API reachable") : esc(m.error || "")) +
    stat("Recording", esc(rec || "unknown"), rec === "recording" ? "warn" : m.online ? "ok" : "", m.recording?.active_meeting_id ? "meeting " + esc(m.recording.active_meeting_id.slice(0, 8)) : "") +
    stat(s?.summary_local ? "Summary model" : "Gemini", s?.summary_local ? "Local" : s?.gemini_key ? "Key set" : "No key", s?.summary_local || s?.gemini_key ? "ok" : "bad", s?.summary_local || s?.gemini_key ? esc(s.model) : "Set GEMINI_API_KEY (or VCS_SUMMARY_MODEL for a local model) and restart") +
    stat("Write access", s?.write?.ok ? "Ready" : s?.write?.reason === "missing" || !s?.write ? "Read-only" : "Key not usable",
      s?.write?.ok ? "ok" : "warn",
      esc(s?.write?.ok ? "Key with write scope" : s?.write?.message || "Set MEETILY_PRO_TOKEN to write summaries back")) +
    stat("Screen capture", cap.active ? "Capturing" : "Idle", cap.active ? "warn" : "",
      cap.active ? esc(cap.title || "") : cap.watch?.title ? "Last window: " + esc(cap.watch.title) : `<a href="#/new">Pick a window</a>`);
  renderAutomation(seq);
  const { runs } = await api("GET", "runs"); if (stale(seq)) return;
  $("#recent").innerHTML = runsTable(runs.slice(0, 5));
  bindRows();
}

const AUTO_DOT = { active: "ok", pending: "warn", registered: "ok", local_target_needed: "warn", disabled: "warn", offline: "bad", error: "bad", starting: "", off: "" };
const EVENT_BADGE = { done: "ok", failed: "warn", offline: "warn", running: "", queued: "" };

async function renderAutomation(seq) {
  const box = $("#auto"); if (!box) return;
  let a; try { a = await api("GET", "automation"); } catch (e) { box.innerHTML = ""; return; }
  if (stale(seq) || !document.body.contains(box)) return;
  const steps = a.state === "active" ? "" :
    `<div class="small muted" style="margin-top:6px">${esc(a.message || "")}</div>`;
  const rows = (a.events || []).map((e) => `<tr><td><span class="mono small">${esc(e.event || "")}</span></td>
      <td class="small muted">${esc((e.meeting_id || "").slice(0, 22))}</td>
      <td><span class="badge ${EVENT_BADGE[e.status] || ""}">${esc(e.status || "")}</span></td>
      <td class="small">${esc((e.log || []).slice(-1)[0] || "")}</td>
      <td class="small muted">${e.received_at ? esc(new Date(e.received_at).toLocaleTimeString([], { timeStyle: "short" })) : ""}</td></tr>`).join("");
  box.innerHTML = `<div class="card-head"><div><h2><span class="dot ${AUTO_DOT[a.state] || ""}" style="display:inline-block;margin-right:8px"></span>Automation</h2>
      <div class="small muted" style="margin-top:2px">${a.state === "active" ? "Listening for Meetily events. " : ""}When a recording starts a popup asks which window to capture (or Skip); when it ends capture stops, a popup asks to generate the summary, and it goes into Meetily if it has none; if Meetily has one you are asked Replace or Keep (popup, also here in the web UI).
      Capture ${a.auto_capture ? "on" : "off"} · write-back ${a.auto_publish ? "on" : "off"} (<a href="#/settings">change</a>)</div>${steps}</div>
      ${a.state === "active" || a.state === "registered" ? '<button class="btn sm" id="atest" type="button">Send test event</button>' : ""}</div>
    <div class="card-body" style="padding-left:0;padding-right:0;padding-bottom:4px">${rows ? `<table><thead><tr><th>Event</th><th>Meeting</th><th>Status</th><th>What happened</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table>`
      : '<div class="small muted" style="padding:0 20px 12px">No events yet.</div>'}</div>`;
  const t = $("#atest");
  if (t) t.onclick = async () => { try { await api("POST", "automation/test"); toast("Test event requested; it appears here when Meetily delivers it."); setTimeout(() => renderAutomation(navSeq), 2500); } catch (e) { toast(e.message, true); } };
}

const STATE_LABEL = { capturing: "Capturing…", extracting: "Extracting screens…", failed: "Failed", pending: "Waiting…" };
const BUSY_LABEL = { summarize: "Generating the summary…", publish: "Writing to Meetily…", extract: "Extracting screens…" };

function runsTable(runs) {
  if (!runs.length) return `<div class="empty">No runs yet. <a href="#/new">Upload a screen recording</a> to start.</div>`;
  return `<table><thead><tr><th>Run</th><th>Created</th><th>Screens</th><th>Summary</th><th></th></tr></thead><tbody>` +
    runs.map((r) => `<tr class="link" data-id="${esc(r.id)}"><td><div style="font-weight:500">${esc(r.id)}</div><div class="small muted">${esc(r.source || "")}</div></td>
      <td class="muted">${esc(ago(r.created))}</td><td>${r.screens}${r.diagrams ? ` <span class="badge">${r.diagrams} diagram${r.diagrams > 1 ? "s" : ""}</span>` : ""}</td>
      <td>${r.state && r.state !== "ready" ? `<span class="badge warn">${esc(STATE_LABEL[r.state] || r.state)}</span>` : r.has_summary ? '<span class="badge ok">Generated</span>' : '<span class="badge">Not yet</span>'}</td><td class="muted">→</td></tr>`).join("") + "</tbody></table>";
}
function bindRows() { document.querySelectorAll("tr.link").forEach((tr) => (tr.onclick = () => (location.hash = "#/runs/" + tr.dataset.id))); }

async function pageRuns(seq) {
  setActive("runs");
  view.innerHTML = `<div class="page-head"><div><h1>Runs</h1><p>Each run is one captured meeting: screenshots, summary and settings.</p></div><a class="btn primary" href="#/new">New run</a></div>
    <div class="card" id="list"></div>`;
  const { runs } = await api("GET", "runs"); if (stale(seq)) return;
  $("#list").innerHTML = runsTable(runs); bindRows();
}

function pageNew(seq) {
  setActive("new");
  view.innerHTML = `<div class="page-head"><div><h1>New run</h1><p>Capture a window during a meeting, or upload a screen recording. Capture, OCR and extraction stay on this computer.</p></div></div>
    <div class="card" id="watch" style="margin-bottom:16px"></div>
    <div class="card"><div class="card-head"><h2>Or upload a recording</h2></div><div class="card-body stack">
      <div class="dropzone" id="drop"><div style="font-weight:500;color:hsl(var(--foreground))">Drop a video here, or click to choose</div><div class="small">mp4, mkv, mov, webm, avi · 1 fps is plenty</div>
        <input type="file" id="file" accept="video/*" hidden></div>
      <div id="up" hidden class="stack"><div class="row"><span id="upname" style="font-weight:500"></span><span class="spacer"></span><span class="muted small" id="uppct"></span></div><div class="log mono" id="log" hidden></div></div>
      <div class="small muted">Sampling and thresholds come from <a href="#/settings">Settings</a>.</div></div></div>`;

  /* ----- window picker / live capture ----- */
  const box = $("#watch");
  let selected = null, timer = null;
  const alive = () => !stale(seq) && document.body.contains(box);

  async function showPicker() {
    clearInterval(timer);
    box.innerHTML = `<div class="card-head"><div><h2>Watch a window</h2><div class="small muted" style="margin-top:2px">Pick the window with the shared content: your slides when presenting, the meeting tab when watching. Only this window is captured, even if covered.</div></div>
      <button class="btn sm" id="wrefresh" type="button">Refresh</button></div><div class="card-body"><div class="win-grid" id="wins"><div class="muted small">Loading windows…</div></div>
      <div id="wwarn"></div><div class="row" style="margin-top:14px"><span class="small muted" id="wsel">No window selected</span><span class="spacer"></span>
      <button class="btn primary" id="wstart" type="button" disabled>Start capture</button></div></div>`;
    $("#wrefresh").onclick = showPicker;
    let d; try { d = await api("GET", "windows"); } catch (e) { $("#wins").innerHTML = `<div class="muted small">${esc(e.message)}</div>`; return; }
    if (!alive()) return;
    if (d.capture.active) return showCapture(d.capture);
    const watch = d.capture.watch || {};
    if (!d.windows.length) { $("#wins").innerHTML = '<div class="muted small">No capturable windows found.</div>'; return; }
    $("#wins").innerHTML = d.windows.map((w) => `<button class="win" type="button" data-h="${w.hwnd}" title="${esc(w.title)}">
        <div class="win-thumb">${w.minimized ? '<span class="small muted">Minimized</span>' : `<img loading="lazy" src="/api/windows/${w.hwnd}/thumb?t=${Date.now()}" alt="">`}</div>
        <div class="win-meta"><div class="win-title">${esc(w.title)}</div><div class="small muted">${esc(w.process)}${watch.title === w.title ? " · last used" : ""}</div></div></button>`).join("");
    box.querySelectorAll(".win").forEach((el) => (el.onclick = () => choose(el, d.windows.find((w) => String(w.hwnd) === el.dataset.h))));
    const last = box.querySelector(`.win[title="${CSS.escape(watch.title || "")}"]`);
    if (last) last.click();
  }

  async function choose(el, w) {
    box.querySelectorAll(".win").forEach((x) => x.classList.toggle("selected", x === el));
    selected = w; $("#wsel").textContent = w.title; $("#wstart").disabled = !!w.minimized; $("#wwarn").innerHTML = "";
    if (w.minimized) { $("#wwarn").innerHTML = '<div class="notice small" style="margin-top:12px">Restore this window first; minimized windows can\'t be captured.</div>'; return; }
    try {   // Google Meet presenter placeholder check (cheap local OCR)
      const r = await api("POST", `windows/${w.hwnd}/check`);
      if (r.warning && selected === w && alive()) $("#wwarn").innerHTML = `<div class="notice small" style="margin-top:12px">${esc(r.warning)}</div>`;
    } catch {}
  }

  async function showCapture(c) {
    box.innerHTML = `<div class="card-head"><div><h2><span class="dot warn" style="display:inline-block;margin-right:8px"></span>Capturing</h2>
        <div class="small muted" style="margin-top:2px">${esc(c.title || "")} · ${esc(c.process || "")}</div></div>
      <button class="btn primary" id="wstop" type="button">Stop and extract</button></div>
      <div class="card-body stack"><img id="wprev" class="preview" alt="Live preview"><div class="row small muted"><span id="welapsed"></span><span class="spacer"></span><span>1 frame per second, screenshots extracted when you stop</span></div>
      <div class="log mono small" id="wlog" hidden></div></div>`;
    const tick = async () => {
      if (!alive()) return clearInterval(timer);
      let st; try { st = await api("GET", "capture"); } catch { return; }
      if (!st.active) { clearInterval(timer); if (st.error) toast(st.error, true); return; }
      $("#welapsed").textContent = `${mmss(st.seconds || 0)} captured${st.window_closed ? " · window closed" : ""}`;
      $("#wprev").src = "/api/capture/preview?t=" + Date.now();
    };
    clearInterval(timer); timer = setInterval(tick, 2000); tick();
    $("#wstop").onclick = async () => {
      $("#wstop").disabled = true; clearInterval(timer);
      try {
        const r = await api("POST", "capture/stop");
        watchJob(r.job, $("#wlog"), () => { toast("Screenshots extracted"); location.hash = "#/runs/" + r.run; });
      } catch (e) { toast(e.message, true); showPicker(); }
    };
  }

  box.addEventListener("click", async (e) => {
    if (e.target.id !== "wstart" || !selected) return;
    e.target.disabled = true;
    try { showCapture(await api("POST", "capture/start", { hwnd: selected.hwnd })); }
    catch (er) { toast(er.message, true); e.target.disabled = false; }
  });
  showPicker();

  /* ----- upload ----- */
  const drop = $("#drop"), file = $("#file");
  drop.onclick = () => file.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
  file.onchange = () => file.files[0] && upload(file.files[0]);
  function upload(f) {
    $("#up").hidden = false; $("#upname").textContent = f.name; drop.hidden = true;
    const x = new XMLHttpRequest();
    x.open("POST", "/api/extract?name=" + encodeURIComponent(f.name)); x.setRequestHeader("X-VCS", "1");
    x.upload.onprogress = (e) => { if (e.lengthComputable) $("#uppct").textContent = "Uploading " + Math.round((e.loaded / e.total) * 100) + "%"; };
    x.onload = () => {
      let d = {}; try { d = JSON.parse(x.responseText); } catch {}
      if (x.status !== 200) { toast(d.error || "Upload failed", true); drop.hidden = false; $("#up").hidden = true; return; }
      $("#uppct").textContent = "Extracting…";
      watchJob(d.job, $("#log"), () => { toast("Extraction finished"); location.hash = "#/runs/" + d.run; });
    };
    x.onerror = () => { toast("Upload failed", true); drop.hidden = false; $("#up").hidden = true; };
    x.send(f);
  }
}

/* a run that is still being captured or extracted: the details fill in as they appear */
async function pageRunProcessing(id, run, seq) {
  setActive("runs");
  const steps = [["capturing", "Capturing the window"], ["extracting", "Extracting screens (OCR, dedup)"], ["ready", "Screens ready"]];
  const at = Math.max(steps.findIndex((x) => x[0] === run.state), 0);
  const job = (run.jobs || []).filter((j) => j.kind === "extract").at(-1);
  view.innerHTML = `<div class="page-head"><div><a class="small muted" href="#/runs">← Runs</a><h1 style="margin-top:6px">${esc(id)}</h1>
      <p>${run.state === "failed" ? "This run did not finish." : "This run is being processed. The page fills in on its own."}</p></div></div>
    <div class="card card-pad stack">
      ${steps.map((x, i) => `<div class="row"><span class="dot ${run.state === "failed" && i === at ? "bad" : i < at ? "ok" : i === at ? "warn" : ""}"></span>
        <span${i > at ? ' class="muted"' : ""}>${esc(x[1])}</span></div>`).join("")}
      ${run.state === "failed" ? `<div class="notice small">${esc(run.error || "Extraction failed")}</div>` : ""}
      ${job?.log?.length ? `<div class="log mono small">${job.log.map((l) => `<div>${esc(l)}</div>`).join("")}</div>` : ""}
    </div>`;
  if (run.state !== "failed") setTimeout(() => { if (!stale(seq)) pageRun(id, seq); }, 2000);
}

async function pageRun(id, seq) {
  setActive("runs");
  let run; try { run = await api("GET", "runs/" + encodeURIComponent(id)); } catch (e) { if (stale(seq)) return; view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (run.state && run.state !== "ready") return pageRunProcessing(id, run, seq);
  const shots = run.screenshots, dur = run.duration || (shots.at(-1)?.end ?? 1);
  let meetings = []; let meetErr = "";
  try { const r = await api("GET", "meetings"); meetings = r.meetings || []; meetErr = r.offline ? "Meetily is offline" : r.error || ""; } catch (e) { meetErr = e.message; }
  const st = status || (await refreshStatus());
  if (stale(seq)) return;
  const cur = run.meta.meeting_id || (run.summary_meta?.meeting_id === "fixture" ? "fixture" : "");
  const opts = `<option value="fixture"${cur === "fixture" ? " selected" : ""}>Demo transcript (built-in fixture)</option>` +
    meetings.map((m) => `<option value="${esc(m.id)}"${m.id === cur ? " selected" : ""}>${esc(m.title || m.id)} · ${esc((m.created_at || "").slice(0, 10))}</option>`).join("");

  view.innerHTML = `<div class="page-head"><div><a class="small muted" href="#/runs">← Runs</a><h1 style="margin-top:6px">${esc(run.id)}</h1>
      <p>${shots.length} screens over ${mmss(dur)}${run.summary_meta ? ` · summary by ${esc(run.summary_meta.model)}` : ""}${run.published ? ` · <span class="badge ok">Written to Meetily ${esc(ago(Date.parse(run.published.at) / 1000))}</span>` : ""}</p></div>
      <div class="row"><button class="btn danger sm" id="del">Delete run</button></div></div>
    <div id="busy" class="notice small" style="margin-bottom:16px" hidden></div>
    <div class="card card-pad stack" style="margin-bottom:16px">
      <div class="row" style="align-items:flex-end">
        <label class="field" style="min-width:300px;flex:1">Meeting transcript<select id="meeting">${opts}</select>
          ${meetErr ? `<span class="hint">Meetily meetings unavailable: ${esc(meetErr)}</span>` : ""}</label>
        <label class="field" style="width:150px">Screen offset (s)<input type="number" id="offset" step="0.5" value="${run.meta.offset ?? 0}"></label>
        <button class="btn" id="preview">Preview input</button>
        <button class="btn primary" id="gen"${st?.gemini_key || st?.summary_local ? "" : " disabled"}>${run.summary ? "Regenerate summary" : "Generate summary"}</button>
        <button class="btn" id="pub"${run.summary && st?.write_key ? "" : " disabled"} title="${!run.summary ? "Generate a summary first" : !st?.write_key ? esc(st?.write?.message || "Set MEETILY_PRO_TOKEN (a key with write scope) and restart the UI server") : "Overwrite the summary in Meetily (the current one is backed up first)"}">${run.published ? "Write again" : "Write to Meetily"}</button>
      </div>
      ${st?.gemini_key || st?.summary_local ? "" : '<div class="notice small">GEMINI_API_KEY isn\'t set in the environment of the UI server. Set it in your terminal and restart <span class="mono">python cli.py serve</span>.</div>'}
      <div class="log mono small" id="log" hidden></div>
    </div>
    <div id="speakers" style="margin-bottom:16px"></div>
    <div style="margin-bottom:12px"><div class="tabs" id="tabs"><button data-t="summary">Summary</button><button data-t="timeline">Timeline</button><button data-t="screens">Screens</button><button data-t="input">Model input</button>${run.backups.length ? '<button data-t="original">Meetily’s original</button>' : ""}</div></div>
    <div id="pane"></div>`;

  const pane = $("#pane");
  const tabs = { summary, timeline, screens, original, input: () => { pane.innerHTML = '<div class="empty">Click “Preview input” to see exactly what is sent to Gemini.</div>'; } };
  function show(t) { document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.t === t)); tabs[t](); }
  document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => show(b.dataset.t)));

  function summary() {
    pane.innerHTML = run.summary
      ? `<div class="card card-pad"><div class="md">${md(run.summary)}</div></div>
         <div class="row" style="margin-top:12px"><button class="btn sm" id="copy">Copy markdown</button><span class="small muted">Saved in the run folder. Not yet written to Meetily.</span></div>`
      : `<div class="card empty">No summary yet. Pick a transcript above and generate one.</div>`;
    const c = $("#copy"); if (c) c.onclick = () => navigator.clipboard.writeText(run.summary).then(() => toast("Copied"));
  }
  async function original() {
    pane.innerHTML = '<div class="empty">Loading…</div>';
    try {
      const b = await api("GET", `runs/${encodeURIComponent(id)}/backup`);
      pane.innerHTML = `<div class="card card-pad"><div class="row small muted" style="margin-bottom:12px"><span>Backup of the summary Meetily had before we wrote ours</span><span class="spacer"></span><span class="mono">${esc(b.file)}</span></div><div class="md">${md(b.text || "(empty)")}</div></div>`;
    } catch (e) { pane.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; }
  }
  async function timeline() {
    pane.innerHTML = '<div class="empty">Loading the transcript…</div>';
    const p = params();
    let tl; try { tl = await api("GET", `runs/${encodeURIComponent(id)}/timeline?meeting_id=${encodeURIComponent(p.meeting_id)}&offset=${p.offset}`); }
    catch (e) { pane.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
    if (stale(seq)) return;
    const total = Math.max(tl.duration, 1);
    tl.speech.forEach((x, i) => (x.key = "s" + i)); tl.screens.forEach((x) => (x.key = "v" + x.id));
    const lane = (items, cls) => items.map((x) => {
      const w = Math.max(((x.end ?? x.t + 2) - x.t) / total * 100, 0.4);
      return `<button class="tseg ${cls(x)}" data-k="${x.key}" style="left:${x.t / total * 100}%;width:${w}%" title="${mmss(x.t)} ${esc((x.text || x.type || "").slice(0, 80))}"></button>`;
    }).join("");
    const rows = [...tl.speech.map((x) => ({ t: x.t, o: 0, x, k: "speech" })), ...tl.screens.map((x) => ({ t: x.t, o: 1, x, k: "screen" }))]
      .sort((a, b) => a.t - b.t || a.o - b.o);
    pane.innerHTML = `<div class="card card-pad" style="margin-bottom:16px">
        <div class="small muted" style="margin-bottom:6px">Speech</div><div class="tlane">${lane(tl.speech, () => "speech")}</div>
        <div class="small muted" style="margin:10px 0 6px">On screen (shifted by ${tl.offset}s)</div><div class="tlane">${lane(tl.screens, (x) => x.type)}</div>
        <div class="timeline-scale small muted"><span>00:00</span><span>${mmss(total)}</span></div></div>
      <div class="card"><div class="tfeed">` + (rows.length ? rows.map(({ x, k }) => k === "speech"
        ? `<div class="trow" id="t-${x.key}"><span class="mono small muted">${mmss(x.t)}</span><div>${x.who ? `<strong>${esc(x.who)}:</strong> ` : ""}${esc(x.text)}</div></div>`
        : `<div class="trow screen" id="t-${x.key}"><span class="mono small muted">${mmss(x.t)}</span><div class="tscreen">
            <img loading="lazy" src="/api/runs/${encodeURIComponent(run.id)}/${x.image}" alt="Screen ${x.id}">
            <div><div class="row"><strong>On screen ${mmss(x.t)} – ${mmss(x.end)}</strong><span class="badge ${x.type === "diagram" ? "warn" : ""}">${esc(x.type)}</span></div>
              ${x.description ? `<div class="small" style="margin-top:6px"><span class="muted">Vision model:</span> ${esc(x.description)}</div>` : ""}
              ${x.text ? `<pre class="mono small muted" style="white-space:pre-wrap;margin:6px 0 0">${esc(x.text)}</pre>` : ""}</div></div></div>`).join("")
        : '<div class="empty">This meeting has no transcript yet.</div>') + "</div></div>";
    pane.querySelectorAll(".tseg").forEach((b) => (b.onclick = () => { const e = $("#t-" + b.dataset.k); e.scrollIntoView({ behavior: "smooth", block: "center" }); e.classList.add("flash"); setTimeout(() => e.classList.remove("flash"), 1200); }));
  }
  function screens() {
    if (!shots.length) { pane.innerHTML = '<div class="card empty">No screenshots were kept.</div>'; return; }
    let t = 0, bar = "";
    shots.forEach((s) => {
      if (s.start > t) bar += `<span class="seg gap" style="flex:${s.start - t}"></span>`;
      bar += `<button class="seg ${s.type}" data-id="${s.id}" style="flex:${Math.max(s.end - s.start, 0.5)}" title="${mmss(s.start)}–${mmss(s.end)} · ${s.type}"></button>`; t = s.end;
    });
    pane.innerHTML = `<div class="card card-pad" style="margin-bottom:16px"><div class="timeline">${bar}</div>
      <div class="timeline-scale small muted"><span>00:00</span><span>${mmss(dur)}</span></div></div>
      <div class="shots">` + shots.map((s) => `<div class="card shot" id="shot-${s.id}"><img loading="lazy" src="/api/runs/${encodeURIComponent(run.id)}/${s.image}" alt="Screen ${s.id}">
        <div class="shot-body"><div class="row"><strong>${mmss(s.start)} – ${mmss(s.end)}</strong>
          <span class="badge ${s.type === "diagram" ? "warn" : ""}">${esc(s.type)}</span>${s.merged_from > 1 ? `<span class="badge">${s.merged_from} builds merged</span>` : ""}</div>
          ${s.text ? `<pre class="mono muted">${esc(s.text)}</pre>` : `<span class="small muted">No text. Sent to Gemini as an image.</span>`}</div></div>`).join("") + "</div>";
    pane.querySelectorAll(".seg[data-id]").forEach((b) => (b.onclick = () => { const e = $("#shot-" + b.dataset.id); e.scrollIntoView({ behavior: "smooth", block: "center" }); e.classList.add("flash"); setTimeout(() => e.classList.remove("flash"), 1200); }));
  }
  show(run.summary ? "summary" : "screens");
  if (run.busy) {                                   // e.g. started from the popup window: follow it here too
    const box = $("#busy"), job = (run.jobs || []).filter((j) => j.status === "running").at(-1);
    box.hidden = false;
    box.innerHTML = `<strong>${esc(BUSY_LABEL[run.busy] || "Working…")}</strong> <span class="muted">${esc((job?.log || []).at(-1) || "")}</span>`;
    setTimeout(() => { if (!stale(seq)) pageRun(id, seq); }, 2000);
  }

  const params = () => ({ meeting_id: $("#meeting").value, offset: parseFloat($("#offset").value) || 0 });
  async function loadSpeakers() {
    const box = $("#speakers"), mid = $("#meeting").value;
    if (!box) return;
    if (!mid || mid === "fixture") { box.innerHTML = ""; return; }
    let d; try { d = await api("GET", `meetings/${encodeURIComponent(mid)}/speakers`); } catch (e) { box.innerHTML = ""; return; }
    if (stale(seq) || $("#meeting").value !== mid) return;
    if (d.speakers.length < 2) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="card"><div class="card-head"><div><h2>Speakers</h2>
        <div class="small muted" style="margin-top:2px">Meetily's API shows voices only as numbers. Identify them in Meetily (it can play each voice), then name them here so the summary uses names.</div></div>
        <button class="btn sm" id="spsave" type="button">Save names</button></div>
      <div class="card-body grid cols-2">` + d.speakers.map((sp) => `<div class="stack" style="gap:6px">
        <label class="field">Speaker ${esc(sp.id)} <span class="hint">${sp.segments} segment${sp.segments === 1 ? "" : "s"} · first at ${mmss(sp.first)}</span>
          <input type="text" data-sp="${esc(sp.id)}" value="${esc(sp.name)}" placeholder="Name"></label>
        ${sp.samples.map((x) => `<div class="small muted"><span class="mono">${mmss(x.time)}</span> “${esc(x.text)}”</div>`).join("")}</div>`).join("") +
      `</div></div>`;
    $("#spsave").onclick = async () => {
      const names = {}; box.querySelectorAll("input[data-sp]").forEach((i) => { if (i.value.trim()) names[i.dataset.sp] = i.value.trim(); });
      try { await api("PUT", `meetings/${encodeURIComponent(mid)}/speakers`, { names }); toast("Speaker names saved. Regenerate the summary to use them."); }
      catch (e) { toast(e.message, true); }
    };
  }
  async function suggestOffset() {
    const mid = $("#meeting").value;
    if (!run.meta.capture_started_at || !mid || mid === "fixture") return;
    try {
      const r = await api("GET", `runs/${encodeURIComponent(id)}/offset?meeting_id=${encodeURIComponent(mid)}`);
      if (r.offset !== null && !stale(seq) && $("#meeting").value === mid) {
        $("#offset").value = r.offset;
        $("#offset").title = `Capture started ${r.offset}s after the recording (from ${r.basis})`;
      }
    } catch {}
  }
  $("#meeting").onchange = () => { loadSpeakers(); suggestOffset(); };
  loadSpeakers();
  if (!run.meta.offset) suggestOffset();
  $("#preview").onclick = async () => {
    const p = params(); try {
      const d = await api("GET", `runs/${encodeURIComponent(id)}/input?meeting_id=${encodeURIComponent(p.meeting_id)}&offset=${p.offset}`);
      document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.t === "input"));
      pane.innerHTML = `<div class="stack"><div class="card card-pad stack"><h3>User message</h3><div class="small muted">${d.diagrams} diagram image(s) are attached after this text.</div><pre class="block mono">${esc(d.input)}</pre></div>
        <div class="card card-pad stack"><h3>System prompt</h3><pre class="block mono">${esc(d.system)}</pre></div></div>`;
    } catch (e) { toast(e.message, true); }
  };
  $("#gen").onclick = async () => {
    const p = params(); $("#gen").disabled = true;
    try {
      const j = await api("POST", `runs/${encodeURIComponent(id)}/summarize`, p);
      watchJob(j.job, $("#log"), () => { toast("Summary generated"); pageRun(id, navSeq); });
    } catch (e) { toast(e.message, true); $("#gen").disabled = false; }
  };
  if ($("#pub")) $("#pub").onclick = async () => {
    const p = params(); let info;
    try { info = await api("GET", `runs/${encodeURIComponent(id)}/publish-info?meeting_id=${encodeURIComponent(p.meeting_id)}`); }
    catch (e) { toast(e.message, true); return; }
    const dlg = h(`<dialog class="dlg"><div class="stack">
      <h2>Write summary to Meetily?</h2>
      <div class="small muted">Meeting <span class="mono">${esc(p.meeting_id)}</span>. Our summary (${info.chars} characters) will <strong>replace</strong> the one in Meetily. The API has no undo, so Meetily's current summary is saved to this run's <span class="mono">backups/</span> folder first.</div>
      ${info.current ? `<details><summary class="small">Meetily's current summary (${esc(info.current.status || "")})</summary><pre class="block mono" style="margin-top:8px;max-height:240px">${esc(info.current.text || "(empty)")}</pre></details>`
                     : `<div class="small muted">Meetily has no summary for this meeting${info.current_error ? " (" + esc(info.current_error) + ")" : ""}, so nothing needs backing up.</div>`}
      <div class="log mono small" id="dlog" hidden></div>
      <div class="row"><span class="spacer"></span><button class="btn" id="dno" type="button">Cancel</button><button class="btn primary" id="dyes" type="button">Replace summary</button></div></div></dialog>`);
    document.body.appendChild(dlg); dlg.showModal();
    dlg.addEventListener("close", () => dlg.remove());
    $("#dno", dlg).onclick = () => dlg.close();
    $("#dyes", dlg).onclick = async () => {
      $("#dyes", dlg).disabled = true;
      try {
        const j = await api("POST", `runs/${encodeURIComponent(id)}/publish`, { meeting_id: p.meeting_id, confirm: true });
        watchJob(j.job, $("#dlog", dlg), () => { toast("Summary written to Meetily"); dlg.close(); pageRun(id, navSeq); });
      } catch (e) { toast(e.message, true); $("#dyes", dlg).disabled = false; }
    };
  };
  $("#del").onclick = async () => {
    if (!confirm(`Delete run "${run.id}" and its screenshots? This can't be undone.`)) return;
    try { await api("DELETE", "runs/" + encodeURIComponent(id)); location.hash = "#/runs"; } catch (e) { toast(e.message, true); }
  };
}

async function pageSettings(seq) {
  setActive("settings");
  const [s, st] = await Promise.all([api("GET", "settings"), refreshStatus()]);
  if (stale(seq)) return;
  const num = (k, label, hint, step = 1) => `<label class="field">${label}<input type="number" name="${k}" step="${step}" value="${s[k]}"><span class="hint">${hint}</span></label>`;
  view.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p>Defaults for new runs and summaries. Secrets stay in environment variables.</p></div></div>
    <form id="f" class="stack">
      <div class="card"><div class="card-head"><h2>Summary</h2></div><div class="card-body grid cols-2">
        <label class="field">Gemini model<input type="text" name="model" value="${esc(s.model)}"><span class="hint">gemini-3.8-flash (default). A Pro model is slower and costlier.</span></label>
        <label class="field">After the summary is written<select name="delete_screenshots_after_summary"><option value="false"${s.delete_screenshots_after_summary ? "" : " selected"}>Keep screenshots</option><option value="true"${s.delete_screenshots_after_summary ? " selected" : ""}>Delete screenshots</option></select>
          <span class="hint">Decided: keep by default. Choose Delete to remove images right after a successful write.</span></label></div></div>
      <div class="card"><div class="card-head"><h2>Automation</h2></div><div class="card-body grid cols-2">
        <label class="field">When a Meetily recording starts<select name="auto_capture"><option value="true"${s.auto_capture ? " selected" : ""}>Capture a window</option><option value="false"${s.auto_capture ? "" : " selected"}>Do nothing</option></select>
          <span class="hint">Capturing starts only when you pick a window (see below). You can also start one by hand on the New run page.</span></label>
        <label class="field">Which window<select name="ask_window"><option value="true"${s.ask_window ? " selected" : ""}>Ask me in a popup (with Skip)</option><option value="false"${s.ask_window ? "" : " selected"}>Use the remembered window</option></select>
          <span class="hint">Remembered window: ${esc((s.watch && s.watch.title) || "none yet. Pick one on the New run page")}.</span></label>
        <label class="field">When the recording ends<select name="ask_generate"><option value="true"${s.ask_generate ? " selected" : ""}>Ask me in a popup: generate the summary?</option><option value="false"${s.ask_generate ? "" : " selected"}>Generate it right away</option></select>
          <span class="hint">Capture stops and screenshots are extracted either way.</span></label>
        <label class="field">Our summary and Meetily<select name="auto_publish"><option value="true"${s.auto_publish ? " selected" : ""}>Write it if Meetily has none, otherwise ask Replace / Keep</option><option value="false"${s.auto_publish ? "" : " selected"}>Only generate it here</option></select>
          <span class="hint">Meetily's own summary is never replaced without your yes, and is backed up first. Needs a write key.</span></label></div></div>
      <div class="card"><div class="card-head"><h2>Screenshot extraction</h2></div><div class="card-body grid cols-2">
        <label class="field">Live capture video<select name="keep_capture_video"><option value="false"${s.keep_capture_video ? "" : " selected"}>Delete after extraction</option><option value="true"${s.keep_capture_video ? " selected" : ""}>Keep in the run folder</option></select>
          <span class="hint">The 1 fps recording of the watched window. Screenshots are kept either way.</span></label>
        ${num("interval", "Sample interval (s)", "How often a frame is checked.", 0.5)}
        ${num("min_dwell", "Minimum time on screen (s)", "Screens shown for less than this are dropped.", 0.5)}
        ${num("hash_threshold", "Change threshold", "Frame-to-frame difference that starts a new screen (of 1024).")}
        ${num("drift_threshold", "Drift threshold", "Difference from a screen's first frame that splits slow scrolling.")}</div></div>
      <div class="card"><div class="card-head"><h2>Environment</h2></div><div class="card-body stack small">
        <div class="row"><span class="dot ${st?.gemini_key ? "ok" : "bad"}"></span><span class="mono">GEMINI_API_KEY</span><span class="muted">${st?.gemini_key ? "set" : "not set"}</span></div>
        <div class="row"><span class="dot ${st?.write?.ok ? "ok" : "warn"}"></span><span class="mono">MEETILY_PRO_TOKEN</span><span class="muted">${st?.write?.ok ? "ready (scopes: " + esc(st.write.scopes.join(", ")) + ")" : esc(st?.write?.message || "not set, read-only")}</span></div>
        <div class="row"><span class="dot ${st?.meetily?.online ? "ok" : "bad"}"></span><span class="mono">Meetily API</span><span class="muted">${st?.meetily?.online ? "http://127.0.0.1:8420 reachable" : esc(st?.meetily?.error || "offline")}</span></div>
        ${st?.meetily?.whoami ? `<div class="muted">Read token scopes: ${esc(st.meetily.whoami.scopes.join(", "))}</div>` : ""}</div></div>
      <div><button class="btn primary" type="submit">Save settings</button></div></form>`;
  $("#f").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target); const body = {};
    for (const [k, v] of f.entries()) body[k] = ["model"].includes(k) ? v : ["delete_screenshots_after_summary", "keep_capture_video", "auto_capture", "ask_window", "ask_generate", "auto_publish"].includes(k) ? v === "true" : parseFloat(v);
    body.hash_threshold = Math.round(body.hash_threshold); body.drift_threshold = Math.round(body.drift_threshold);
    try { await api("PUT", "settings", body); toast("Settings saved"); } catch (er) { toast(er.message, true); }
  };
}

/* ---------- decisions waiting for the user: Meetily already has a summary ---------- */
let askOpen = false; const askLater = new Set();
async function checkPending() {
  if (askOpen) return;
  let d; try { d = await api("GET", "pending"); } catch { return; }
  const p = (d.pending || []).find((x) => !askLater.has(x.run + x.at));
  if (!p || askOpen) return;
  askOpen = true;
  const q = encodeURIComponent;
  const [info, run] = await Promise.all([
    api("GET", `runs/${q(p.run)}/publish-info?meeting_id=${q(p.meeting_id)}`).catch(() => null),
    api("GET", `runs/${q(p.run)}`).catch(() => null)]);
  const name = p.title ? `“${esc(p.title)}”` : `meeting <span class="mono">${esc(String(p.meeting_id).slice(0, 12))}</span>`;
  const why = p.reason === "replaced" ? "Meetily replaced the summary we wrote with its own." : "Meetily already has its own summary for this meeting.";
  const dlg = h(`<dialog class="dlg"><div class="stack">
    <div><h2>Overwrite Meetily's summary?</h2>
    <div class="small muted" style="margin-top:6px">The recording of ${name} has ended and our summary (it includes what was on screen) is ready. ${why}
      If you overwrite it, Meetily's current summary is saved to this run's <span class="mono">backups/</span> folder first.</div></div>
    ${info?.current?.text ? `<details><summary class="small">Meetily's current summary (${info.current.text.length} characters)</summary><pre class="block mono" style="margin-top:8px;max-height:220px">${esc(info.current.text)}</pre></details>` : ""}
    ${run?.summary ? `<details><summary class="small">Our summary (${run.summary.length} characters)</summary><pre class="block mono" style="margin-top:8px;max-height:220px">${esc(run.summary)}</pre></details>` : ""}
    <div class="log mono small" id="alog" hidden></div>
    <div class="row"><button class="btn ghost sm" id="alater" type="button">Decide later</button><span class="spacer"></span>
      <button class="btn" id="akeep" type="button">Keep Meetily's</button><button class="btn primary" id="aover" type="button">Overwrite with ours</button></div></div></dialog>`);
  document.body.appendChild(dlg); dlg.showModal();
  let settled = false;
  dlg.addEventListener("close", () => { if (!settled) askLater.add(p.run + p.at); dlg.remove(); askOpen = false; setTimeout(checkPending, 1500); });
  $("#alater", dlg).onclick = () => dlg.close();
  const busy = (on) => dlg.querySelectorAll("button").forEach((b) => (b.disabled = on));
  $("#akeep", dlg).onclick = async () => {
    busy(true);
    try { await api("POST", `runs/${q(p.run)}/overwrite`, { action: "keep" }); settled = true; toast("Kept Meetily's summary"); dlg.close(); }
    catch (e) { toast(e.message, true); busy(false); }
  };
  $("#aover", dlg).onclick = async () => {
    busy(true);
    try {
      const j = await api("POST", `runs/${q(p.run)}/overwrite`, { action: "overwrite" });
      watchJob(j.job, $("#alog", dlg), () => { settled = true; toast("Meetily's summary replaced with ours"); dlg.close(); if (location.hash === "#/runs/" + p.run) route(); });
    } catch (e) { toast(e.message, true); busy(false); }
  };
}
checkPending(); setInterval(checkPending, 4000);

/* ---------- VLM test page ---------- */
async function pageVlm(seq) {
  setActive("vlm");
  view.innerHTML = `<div class="page-head"><div><h1>VLM test</h1><p>Drop a screenshot: see what OCR reads and what each local vision model says, with timings. Runs on your machine only.</p></div></div>
    <div class="grid cols-2">
      <div class="card"><div class="card-body">
        <div id="vdrop" class="empty" style="border:1px dashed hsl(var(--border));border-radius:var(--radius);padding:28px 12px;cursor:pointer">
          <div id="vprev">Drop an image here, paste (Ctrl+V), or click to choose</div></div>
        <input id="vfile" type="file" accept="image/png,image/jpeg,image/webp" hidden>
        <div style="margin-top:14px"><div class="small muted" style="margin-bottom:6px">Models (pick up to 4 to compare)</div><div id="vmodels" class="small">Loading…</div></div>
        <div style="margin-top:14px"><div class="small muted" style="margin-bottom:6px">Device</div>
          <select id="vgpu"><option value="">Ollama default (GPU if it fits)</option><option value="0">CPU only</option></select></div>
        <div style="margin-top:14px"><div class="small muted" style="margin-bottom:6px">Prompt</div>
          <textarea id="vprompt" rows="4" style="width:100%;font:inherit;padding:8px;border:1px solid hsl(var(--border));border-radius:var(--radius);background:transparent;color:inherit"></textarea></div>
        <div style="margin-top:14px;display:flex;gap:10px;align-items:center"><button class="btn primary" id="vrun" type="button" disabled>Run</button><span id="vstat" class="small muted"></span></div>
      </div></div>
      <div class="card"><div class="card-head"><h2>OCR</h2><span id="vflag"></span></div><div class="card-body"><pre id="vocr" class="mono small" style="white-space:pre-wrap;margin:0">Run a test to see the OCR text.</pre></div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h2>Model output</h2></div><div id="vres" class="card-body"><div class="small muted">No results yet.</div></div></div>`;
  let info; try { info = await api("GET", "vlm/models"); } catch (e) { info = { models: [] }; }
  if (stale(seq)) return;
  $("#vprompt").value = info.prompt || "";
  $("#vgpu").value = info.num_gpu === "0" ? "0" : "";
  $("#vmodels").innerHTML = info.models.length
    ? info.models.map((m) => `<label style="display:block;margin:3px 0"><input type="checkbox" value="${esc(m)}" ${m === info.default ? "checked" : ""}> <span class="mono">${esc(m)}</span>${m === info.default ? ' <span class="badge ok">pipeline default</span>' : ""}</label>`).join("")
    : '<span class="muted">Ollama is not reachable. Start it, then reload this page.</span>';
  let blob = null;
  const setImg = (f) => {
    if (!f || !/^image\//.test(f.type)) return toast("Use a PNG, JPG or WebP image", true);
    blob = f; $("#vprev").innerHTML = `<img src="${URL.createObjectURL(f)}" style="max-width:100%;max-height:220px;border-radius:6px"><div class="small muted" style="margin-top:6px">${esc(f.name || "pasted image")}</div>`;
    $("#vrun").disabled = false;
  };
  const drop = $("#vdrop");
  drop.onclick = () => $("#vfile").click();
  $("#vfile").onchange = (e) => setImg(e.target.files[0]);
  drop.ondragover = (e) => e.preventDefault();
  drop.ondrop = (e) => { e.preventDefault(); setImg(e.dataTransfer.files[0]); };
  document.onpaste = (e) => { if (location.hash === "#/vlm") setImg([...(e.clipboardData?.files || [])][0]); };
  $("#vrun").onclick = async () => {
    const models = [...document.querySelectorAll("#vmodels input:checked")].map((i) => i.value);
    if (!blob) return; if (!models.length) return toast("Pick at least one model", true);
    const btn = $("#vrun"); btn.disabled = true;
    const t0 = Date.now(); const tick = setInterval(() => ($("#vstat").textContent = `Running… ${Math.round((Date.now() - t0) / 1000)} s (models run one after another)`), 500);
    $("#vres").innerHTML = '<div class="small muted">Working…</div>';
    try {
      const qs = new URLSearchParams({ models: models.join(","), gpu: $("#vgpu").value, prompt: $("#vprompt").value });
      const r = await api("POST", "vlm/test?" + qs, undefined, blob);
      if (stale(seq)) return;
      $("#vocr").textContent = r.ocr || "(no text found)";
      $("#vflag").innerHTML = `<span class="badge ${r.diagram ? "warn" : "ok"}">${r.diagram ? "diagram: VLM would run" : "text slide: OCR only"}</span> <span class="small muted">${r.alnum} chars, ink ${r.ink}, OCR ${r.ocr_seconds} s</span>`;
      $("#vres").innerHTML = `<table><thead><tr><th>Model</th><th>Time</th><th>Output</th></tr></thead><tbody>` + r.results.map((x) =>
        `<tr><td class="mono small">${esc(x.model)}</td><td>${x.seconds} s</td><td>${x.error ? `<span style="color:hsl(var(--destructive))">${esc(x.error)}</span>` : esc(x.text) || '<span class="muted">(empty)</span>'}</td></tr>`).join("") + "</tbody></table>";
    } catch (e) { toast(e.message, true); $("#vres").innerHTML = `<div class="small muted">${esc(e.message)}</div>`; }
    finally { clearInterval(tick); $("#vstat").textContent = ""; btn.disabled = false; }
  };
}

/* ---------- router ---------- */
async function route() {
  const seq = ++navSeq;
  const [, a, b] = location.hash.replace(/^#/, "").split("/");
  try {
    if (a === "runs" && b) await pageRun(decodeURIComponent(b), seq);
    else if (a === "runs") await pageRuns(seq);
    else if (a === "new") pageNew(seq);
    else if (a === "vlm") await pageVlm(seq);
    else if (a === "settings") await pageSettings(seq);
    else await pageHome(seq);
  } catch (e) { if (!stale(seq)) view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; }
  if (!stale(seq)) window.scrollTo(0, 0);
}
addEventListener("hashchange", route);
refreshStatus(); setInterval(refreshStatus, 15000);
route();
