/* Panel front end: render a form per command, run it, stream the output back.
   No framework and no build step — the page is served straight from the package. */

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const escapeHtml = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const state = {
  commands: [],
  current: null,
  job: null,
  source: null,
  documents: [],
  since: 0,
  ticker: null,
};

/* ── ANSI → HTML ───────────────────────────────────────────────────────────
   The child process writes 16-colour ANSI (see ui/runner.py). Colours are mapped
   onto the theme's tokens rather than fixed hexes so both themes stay readable. */
const ANSI_COLOR = {
  30: "var(--muted)", 31: "var(--neg)", 32: "var(--pos)", 33: "var(--caution)",
  34: "var(--accent)", 35: "#a97bd6", 36: "var(--accent)", 37: "var(--text)",
  90: "var(--muted)", 91: "var(--neg)", 92: "var(--pos)", 93: "var(--caution)",
  94: "var(--accent)", 95: "#c39ae6", 96: "var(--accent)", 97: "var(--text)",
};

function ansiToHtml(line) {
  let out = "";
  let open = false;
  const style = { color: null, bold: false, dim: false, italic: false, underline: false };

  const flush = () => {
    if (open) { out += "</span>"; open = false; }
    const bits = [];
    if (style.color) bits.push(`color:${style.color}`);
    if (style.bold) bits.push("font-weight:600");
    if (style.dim && !style.color) bits.push("color:var(--muted)");
    if (style.dim) bits.push("opacity:.8");
    if (style.italic) bits.push("font-style:italic");
    if (style.underline) bits.push("text-decoration:underline");
    if (bits.length) { out += `<span style="${bits.join(";")}">`; open = true; }
  };

  const re = /\x1b\[([0-9;]*)([A-Za-z])/g;
  let last = 0, match;
  while ((match = re.exec(line)) !== null) {
    out += escapeHtml(line.slice(last, match.index));
    last = re.lastIndex;
    if (match[2] !== "m") continue;              // cursor moves etc. — drop them
    const codes = match[1].split(";").filter((c) => c !== "").map(Number);
    if (!codes.length) codes.push(0);
    for (let i = 0; i < codes.length; i++) {
      const code = codes[i];
      if (code === 0) { style.color = null; style.bold = style.dim = style.italic = style.underline = false; }
      else if (code === 1) style.bold = true;
      else if (code === 2) style.dim = true;
      else if (code === 3) style.italic = true;
      else if (code === 4) style.underline = true;
      else if (code === 22) { style.bold = false; style.dim = false; }
      else if (code === 23) style.italic = false;
      else if (code === 24) style.underline = false;
      else if (code === 39) style.color = null;
      else if (ANSI_COLOR[code]) style.color = ANSI_COLOR[code];
      else if (code === 38 || code === 48) {      // extended colour: skip its arguments
        if (codes[i + 1] === 5) i += 2;
        else if (codes[i + 1] === 2) i += 4;
      }
    }
    flush();
  }
  out += escapeHtml(line.slice(last));
  if (open) out += "</span>";
  return out;
}

/* ── Markdown → HTML ───────────────────────────────────────────────────────
   A deliberately small subset: exactly what report/brief.py emits — headings,
   tables with alignment, lists, blockquotes, rules and inline emphasis. */
function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
}

function renderMarkdown(md) {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;

  const isTableSep = (s) => /^\|(\s*:?-{2,}:?\s*\|)+$/.test(s.trim());
  const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (/^---+$/.test(line.trim())) { out.push("<hr>"); i++; continue; }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++; continue;
    }

    if (line.startsWith("> ")) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith("> ")) quote.push(lines[i].slice(2)), i++;
      out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
      continue;
    }

    if (line.trim().startsWith("|") && isTableSep(lines[i + 1] || "")) {
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) => (c.endsWith(":") && !c.startsWith(":") ? "r" : ""));
      i += 2;
      const body = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) body.push(cells(lines[i])), i++;
      const th = head.map((c, n) => `<th class="${align[n] || ""}">${inline(c)}</th>`).join("");
      const rows = body
        .map((r) => "<tr>" + r.map((c, n) => `<td class="${align[n] || ""}">${inline(c)}</td>`).join("") + "</tr>")
        .join("");
      out.push(`<table><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table>`);
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) items.push(lines[i].replace(/^[-*]\s+/, "")), i++;
      out.push("<ul>" + items.map((t) => `<li>${inline(t)}</li>`).join("") + "</ul>");
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|[-*]\s|\||>\s|---+$)/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

/* ── status header ─────────────────────────────────────────────────────── */
async function loadStatus() {
  const status = await fetch("/api/status").then((r) => r.json());
  const chips = $("#status-chips");
  chips.textContent = "";
  const add = (text, cls) => chips.appendChild(el("span", `chip ${cls || ""}`, text));

  add(`${status.universe}`);
  add(status.latest_stored ? `data → ${status.latest_stored}` : "no stored data", status.latest_stored ? "" : "off");
  add(`${status.db_mb} MB db`);
  add(`${status.brief_count} briefs`);
  // Presence in .env is not validity — Upstox tokens expire daily, and only Doctor (or a
  // run) actually asks. So the chip reports the token, never the tier.
  const token = $("#status-chips").appendChild(
    el("span", `chip ${status.keys["Upstox token"] ? "on" : "off"}`,
       status.keys["Upstox token"] ? "upstox token set" : "no upstox token"));
  token.title = status.keys["Upstox token"]
    ? "A token is in .env. Tokens expire daily — run Doctor to see whether it still works."
    : "Runs will use the archive/delayed tier. Run Upstox auth for the live tier.";
  const llm = Object.entries(status.keys).filter(([k, v]) => k !== "Upstox token" && v).length;
  add(llm ? `${llm} LLM keys` : "no LLM keys", llm ? "on" : "off");
}

/* ── command rail and form ─────────────────────────────────────────────── */
function buildRail(groups) {
  const rail = $("#command-rail");
  rail.textContent = "";
  for (const group of groups) {
    rail.appendChild(el("div", "group-label", group));
    for (const cmd of state.commands.filter((c) => c.group === group)) {
      const button = el("button", "", cmd.title);
      button.appendChild(el("span", "meta", cmd.runtime));
      button.onclick = () => selectCommand(cmd.id);
      button.dataset.id = cmd.id;
      rail.appendChild(button);
    }
  }
}

function storedValues(id) {
  try { return JSON.parse(localStorage.getItem(`asym.form.${id}`)) || {}; }
  catch { return {}; }
}
function storeValues(id, values) {
  localStorage.setItem(`asym.form.${id}`, JSON.stringify(values));
}

function selectCommand(id) {
  const cmd = state.commands.find((c) => c.id === id);
  if (!cmd) return;
  state.current = cmd;
  localStorage.setItem("asym.command", id);
  for (const b of document.querySelectorAll("#command-rail button")) {
    b.classList.toggle("is-on", b.dataset.id === id);
  }

  $("#cmd-title").textContent = cmd.title;
  $("#cmd-blurb").textContent = cmd.blurb;
  $("#cmd-cli").textContent = cmd.cli;
  const danger = $("#cmd-danger");
  danger.hidden = !cmd.danger;
  danger.textContent = cmd.danger || "";
  $("#run-note").textContent = cmd.runtime ? `typically ${cmd.runtime}` : "";

  const form = $("#cmd-form");
  form.textContent = "";
  const saved = storedValues(id);
  for (const f of cmd.fields) {
    const value = f.name in saved ? saved[f.name] : f.default;
    form.appendChild(fieldNode(f, value));
  }
}

function fieldNode(f, value) {
  const wrap = el("div", "field");
  const id = `f-${f.name}`;

  if (f.kind === "flag" || f.kind === "toggle") {
    wrap.className = "field switch";
    const label = el("label", "check");
    const input = el("input");
    input.type = "checkbox"; input.id = id; input.checked = !!value;
    label.appendChild(input);
    label.appendChild(el("span", "", f.label));
    wrap.appendChild(label);
    if (f.help) wrap.appendChild(el("span", "hint", f.help));
    return wrap;
  }

  wrap.appendChild(el("label", "", f.label)).htmlFor = id;

  if (f.kind === "choice") {
    const select = el("select");
    select.id = id;
    for (const choice of f.choices) {
      const option = el("option", "", choice);
      option.value = choice;
      option.selected = choice === value;
      select.appendChild(option);
    }
    wrap.appendChild(select);
  } else if (f.kind === "multi") {
    const chosen = new Set(Array.isArray(value) ? value : []);
    const box = el("div", "opts");
    box.id = id;
    for (const choice of f.choices) {
      const label = el("label", "check");
      const input = el("input");
      input.type = "checkbox"; input.value = choice; input.checked = chosen.has(choice);
      label.appendChild(input);
      label.appendChild(el("span", "", choice));
      box.appendChild(label);
    }
    wrap.appendChild(box);
  } else {
    const input = el("input");
    input.id = id;
    input.type = f.kind === "int" || f.kind === "float" ? "number" : "text";
    if (f.kind === "float") input.step = "any";
    if (f.kind === "date") input.placeholder = "YYYY-MM-DD (blank = latest)";
    input.value = value == null ? "" : value;
    wrap.appendChild(input);
  }
  if (f.help) wrap.appendChild(el("span", "hint", f.help));
  return wrap;
}

function collectValues() {
  const values = {};
  for (const f of state.current.fields) {
    const node = document.getElementById(`f-${f.name}`);
    if (!node) continue;
    if (f.kind === "flag" || f.kind === "toggle") values[f.name] = node.checked;
    else if (f.kind === "multi")
      values[f.name] = [...node.querySelectorAll("input:checked")].map((i) => i.value);
    else values[f.name] = node.value;
  }
  return values;
}

/* ── running ───────────────────────────────────────────────────────────── */
const consoleNode = () => $("#console");

/* How many characters the log pane fits, measured rather than assumed — the child
   process lays its tables out to exactly this width. */
function consoleColumns() {
  const box = consoleNode();
  const probe = el("span", "", "0".repeat(100));
  probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
  box.appendChild(probe);
  const charWidth = probe.getBoundingClientRect().width / 100;
  probe.remove();
  if (!charWidth) return 150;
  return Math.max(90, Math.floor((box.clientWidth - 32) / charWidth));
}

function clock(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/* A V3 scan can spend twenty seconds loading bars before it prints anything, so the pill
   counts while it waits — otherwise a working run looks like a hung one. */
function setJobState(status, since) {
  const pill = $("#job-state");
  const running = status === "running" || status === "queued";
  if (since != null) state.since = since;
  pill.className = `pill ${status || ""}`;
  pill.textContent = status ? (running ? `${status} ${clock((Date.now() - state.since) / 1000)}` : status) : "";
  $("#run").disabled = running;
  $("#cancel").hidden = !running;
  clearInterval(state.ticker);
  state.ticker = null;
  if (running) {
    state.ticker = setInterval(() => {
      pill.textContent = `${status} ${clock((Date.now() - state.since) / 1000)}`;
    }, 1000);
  }
}

function appendLines(lines) {
  if (!lines.length) return;
  const box = consoleNode();
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.insertAdjacentHTML("beforeend", lines.map(ansiToHtml).join("\n") + "\n");
  if ($("#follow").checked && atBottom) box.scrollTop = box.scrollHeight;
}

async function run() {
  const cmd = state.current;
  if (!cmd) return;
  const values = collectValues();
  storeValues(cmd.id, values);

  const response = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: cmd.id, values, width: consoleColumns() }),
  });
  const job = await response.json();
  if (!response.ok) {
    consoleNode().innerHTML = `<span style="color:var(--neg)">${escapeHtml(job.error || "could not start")}</span>`;
    return;
  }
  consoleNode().innerHTML = `<span style="color:var(--muted)">$ ${escapeHtml(job.cli)}</span>\n`;
  $("#run-note").textContent = cmd.runtime ? `typically ${cmd.runtime}` : "";
  $("#run-note").classList.remove("err");
  attach(job);
  loadRuns();
}

function attach(job, from = 0) {
  if (state.source) { state.source.close(); state.source = null; }
  state.job = job;
  setJobState(job.status, Date.now() - (job.elapsed || 0) * 1000);
  const source = new EventSource(`/api/jobs/${job.id}/stream?from=${from}`);
  state.source = source;
  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    appendLines(data.lines || []);
    if (data.status) setJobState(data.status);
    if (data.done) {
      setJobState(data.status);
      source.close();
      state.source = null;
      const note = $("#run-note");
      note.textContent = data.error || `finished in ${clock(data.elapsed)}`;
      note.classList.toggle("err", data.status === "failed");
      loadRuns();
      loadStatus();
      if ((state.current?.outputs || []).length) loadDocuments();
    }
  };
  source.onerror = () => { source.close(); state.source = null; };
}

async function openJob(id) {
  const job = await fetch(`/api/jobs/${id}`).then((r) => r.json());
  showView("run");
  if (job.command) {
    const cmd = state.commands.find((c) => c.id === job.command);
    if (cmd) selectCommand(cmd.id);
  }
  consoleNode().innerHTML = `<span style="color:var(--muted)">$ ${escapeHtml(job.cli)}</span>\n`;
  appendLines(job.lines || []);
  setJobState(job.status, Date.now() - (job.elapsed || 0) * 1000);
  if (job.status === "running" || job.status === "queued") attach(job, job.next || 0);
}

/* ── run history ───────────────────────────────────────────────────────── */
async function loadRuns() {
  const { jobs } = await fetch("/api/jobs").then((r) => r.json());
  const body = $("#runs-table tbody");
  body.textContent = "";
  for (const job of jobs) {
    const tr = el("tr");
    tr.onclick = () => openJob(job.id);
    tr.appendChild(el("td", "", new Date(job.created * 1000).toLocaleTimeString()));
    const cmd = el("td");
    cmd.appendChild(el("code", "", job.cli));
    tr.appendChild(cmd);
    const status = el("td");
    status.appendChild(el("span", `pill ${job.status}`, job.status));
    tr.appendChild(status);
    tr.appendChild(el("td", "num", `${job.elapsed}s`));
    tr.appendChild(el("td", "num", String(job.line_count)));
    body.appendChild(tr);
  }
  if (!jobs.length) {
    const tr = el("tr");
    const td = el("td", "dim", "Nothing run yet this session.");
    td.colSpan = 5;
    tr.appendChild(td);
    body.appendChild(tr);
  }
}

/* ── documents ─────────────────────────────────────────────────────────── */
async function loadDocuments() {
  const { documents } = await fetch("/api/documents").then((r) => r.json());
  state.documents = documents;
  const rail = $("#doc-rail");
  rail.textContent = "";
  const groups = [["brief", "Briefs (Markdown)"], ["page", "Published pages"]];
  for (const [kind, label] of groups) {
    const items = documents.filter((d) => d.kind === kind);
    if (!items.length) continue;
    rail.appendChild(el("div", "group-label", label));
    for (const doc of items) {
      const button = el("button", "", doc.title);
      button.appendChild(el("span", "meta", new Date(doc.modified * 1000).toLocaleString()));
      button.dataset.name = doc.name;
      button.onclick = () => openDocument(doc);
      rail.appendChild(button);
    }
  }
}

async function openDocument(doc) {
  for (const b of document.querySelectorAll("#doc-rail button")) {
    b.classList.toggle("is-on", b.dataset.name === doc.name);
  }
  $("#doc-title").textContent = doc.title;
  $("#doc-meta").textContent =
    `${doc.kind === "brief" ? "data/briefs" : "public"}/${doc.name} · ${new Date(doc.modified * 1000).toLocaleString()}`;
  const body = $("#doc-body");
  const frame = $("#doc-frame");
  if (doc.kind === "page") {
    body.hidden = true;
    frame.hidden = false;
    frame.src = `/page/${encodeURIComponent(doc.name)}`;
    return;
  }
  frame.hidden = true;
  frame.removeAttribute("src");
  body.hidden = false;
  const { text } = await fetch(`/api/documents/${encodeURIComponent(doc.name)}`).then((r) => r.json());
  body.innerHTML = renderMarkdown(text);
}

/* ── views and boot ────────────────────────────────────────────────────── */
function showView(name) {
  for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("is-on", tab.dataset.view === name);
  for (const view of document.querySelectorAll(".view")) view.classList.toggle("is-on", view.dataset.view === name);
  if (name === "runs") loadRuns();
  if (name === "docs") loadDocuments();
}

function initTheme() {
  const saved = localStorage.getItem("asym.theme") || "dark";
  document.documentElement.dataset.theme = saved;
  $("#theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("asym.theme", next);
  };
}

async function boot() {
  initTheme();
  for (const tab of document.querySelectorAll(".tab")) tab.onclick = () => showView(tab.dataset.view);
  $("#run").onclick = run;
  $("#cancel").onclick = async () => {
    if (state.job) await fetch(`/api/jobs/${state.job.id}/cancel`, { method: "POST" });
  };
  $("#copy").onclick = () => navigator.clipboard.writeText(consoleNode().innerText);

  const { commands, groups } = await fetch("/api/commands").then((r) => r.json());
  state.commands = commands;
  buildRail(groups);
  selectCommand(localStorage.getItem("asym.command") || commands[0].id);
  setJobState("");
  loadStatus();
  loadRuns();

  // A run started before a reload is still going; reattach to it.
  const { active } = await fetch("/api/jobs").then((r) => r.json());
  if (active) openJob(active.id);
}

boot();
