interface Bridge {
  state(): Promise<any>;
  demo(): Promise<any>;
  demoRecorded(): Promise<any>;
  trace(table: string, key: string): Promise<any>;
  traceRecorded(): Promise<any>;
  change(id: string): Promise<any>;
  changeRecorded(id: string): Promise<any>;
  readiness(): Promise<any>;
  chooseSetting(kind: string): Promise<any>;
  clearSetting(kind: string): Promise<any>;
  setDsn(dsn: string, remember: boolean): Promise<any>;
  clearDsn(): Promise<any>;
}
declare const clearledger: Bridge;

const view = document.getElementById("view")!;
let st: any = null;
let lastDemo: any = null;
let lastTrace: any = null;
let lastChange: any = null;
let settingsMsg = "";

// Every string from the core goes in with textContent, never as markup.
function el(tag: string, attrs: Record<string, string> = {}, ...kids: Array<Node | string | null>): HTMLElement {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  for (const k of kids) if (k !== null) e.append(k);
  return e;
}
const label = (text: string, cls = text) => el("span", { class: `label ${cls.replace(/\s+/g, "")}` }, text);
const errBox = (e: any) => el("div", { class: "card" }, el("pre", { class: "err" }, e.message), e.fix ? el("div", {}, el("strong", {}, "Fix"), el("pre", { class: "mono" }, e.fix)) : null);
const button = (text: string, cls: string, fn: () => void) => { const b = el("button", { class: cls }, text); b.addEventListener("click", fn); return b; };

async function loadState(): Promise<void> { st = await clearledger.state(); }

// ---------- Demo ----------

function renderDemo(out: HTMLElement, r: any): void {
  if (!r.ok) {
    out.replaceChildren(errBox(r.error), el("div", { class: "actions" }, button("Show the recording instead", "", async () => renderDemo(out, (lastDemo = await clearledger.demoRecorded())))));
    return;
  }
  const v = r.value.view;
  const rec = v.origin === "recorded";
  const parts: Node[] = [el("div", { class: `banner ${rec ? "recorded" : ""}` }, rec ? label("RECORDED") : label("RAN NOW", "LIVE"), v.banner)];
  if (r.value.why) parts.push(el("pre", { class: "muted" }, r.value.why));
  const seeded = el("div", { class: `card ${rec ? "recorded" : ""}` }, el("h2", {}, "1. Known failures in collections, planted on purpose"),
    el("p", { class: "muted" }, rec ? "From the recording." : "Run now through the collections agent's real rules, on planted cases (not a database)."));
  for (const s of v.seeded) seeded.append(el("div", { class: "line" }, label(s.status), el("strong", {}, s.name), el("div", { class: "muted" }, s.detail)));
  parts.push(seeded);
  parts.push(el("div", { class: "card recorded" }, el("h2", {}, "2. Results from earlier runs on the full system"), el("p", {}, label("RECORDED"), "Read from result files the full stack wrote earlier, as the demo prints them."), ...v.recordedRuns.map((l: string) => el("div", { class: "mono" }, l))));
  const walk = el("div", { class: `card ${rec ? "recorded" : ""}` }, el("h2", {}, "3. One question end to end: who owes us money more than 90 days late, and can we show why"));
  for (const s of v.walkthrough) {
    const box = el("div", { class: "line" }, el("div", {}, label(s.mode), el("strong", {}, `${s.step}. ${s.name}`), s.ok ? null : el("span", { class: "err" }, "  FAILED")), el("div", { class: "muted" }, s.reason));
    if (s.text) box.append(el("pre", { class: "mono scroll" }, s.text));
    else if (s.lines.length) box.append(el("pre", { class: "mono scroll" }, s.lines.join("\n")));
    walk.append(box);
  }
  walk.append(el("p", { class: "muted" }, `Summary: ${v.summary}`));
  walk.append(el("p", { class: "src" }, "LIVE: ran now against the real thing. STUB: ran now with a scripted stand-in for the language model. RECORDED: read from a file an earlier real run wrote. SKIPPED: not run, with the reason."));
  parts.push(walk);
  if (v.scorecard.length) parts.push(el("div", { class: "card" }, el("h2", {}, "Scorecard"), ...v.scorecard.map((l: string) => el("div", { class: "line" }, l))));
  if (r.value.commands?.length) parts.push(el("p", { class: "src" }, `Commands: ${r.value.commands.join("   ")}`));
  out.replaceChildren(...parts);
}

async function demoScreen(): Promise<void> {
  const out = el("div", {});
  const run = async () => {
    out.replaceChildren(el("p", { class: "muted" }, "Running the demo on this machine (about 10 seconds)..."));
    lastDemo = await clearledger.demo();
    renderDemo(out, lastDemo);
  };
  const intro = el("p", { class: "muted" }, "A simulated client engagement: Halden Industrial Supply, an invented distributor on Odoo. The demo needs no database. It plants twelve known failures and shows each one handled, then follows one finance question through four steps, each labelled for what actually ran.");
  view.replaceChildren(el("h1", {}, "Demo"), intro,
    el("div", { class: "actions" }, button("Run the demo", "primary", () => void run()), button("Show the recording", "", async () => renderDemo(out, (lastDemo = await clearledger.demoRecorded())))), out);
  if (lastDemo) renderDemo(out, lastDemo);
}

// ---------- Trace ----------

function traceTable(t: any): HTMLElement {
  if (!t.columns.length) return el("div", {}, el("h2", {}, t.title), el("p", { class: "muted" }, "(none)"));
  const money = /^-?[\d,]+\.\d\d$/;
  const head = el("tr", {}, ...t.columns.map((c: string) => el("th", {}, c)));
  const rows = t.rows.map((r: string[]) => el("tr", {}, ...r.map((cell) => el("td", money.test(cell) ? { class: "num" } : {}, cell))));
  return el("div", {}, el("h2", {}, t.title), el("div", { class: "scroll" }, el("table", { class: "mono" }, el("thead", {}, head), el("tbody", {}, ...rows))));
}

function renderTrace(out: HTMLElement, r: any): void {
  if (!r.ok) {
    out.replaceChildren(errBox(r.error), el("div", { class: "actions" }, button("Show the recorded example", "", async () => renderTrace(out, (lastTrace = await clearledger.traceRecorded())))));
    return;
  }
  const a = r.value;
  const rec = a.origin === "recorded";
  const v = a.view;
  out.replaceChildren(
    el("div", { class: `banner ${rec ? "recorded" : ""}` }, rec ? label("RECORDED") : label("LIVE"), a.note),
    el("div", { class: `card ${rec ? "recorded" : ""}` },
      el("div", { class: "verdict" }, v.headline),
      ...v.notes.map((n: string) => el("div", { class: "muted" }, n)),
      el("p", {}, v.addsUp ? el("span", { class: "ok" }, "The records below add up to the number.") : el("span", { class: "err" }, "The records below DO NOT add up to the number.")),
      ...v.tables.map(traceTable),
      el("h2", {}, "Checks"),
      ...v.checks.map((c: any) => el("div", { class: "line" }, label(c.ok ? "OK" : "DOES NOT ADD UP", c.ok ? "PASS" : "FAIL"), `${c.label}: ${c.claimed} = sum of the records (${c.sum})`))),
    el("p", { class: "src" }, `Command: ${a.command}. Each source_ref is the Odoo record and the exact version the number used.`),
  );
}

async function traceScreen(): Promise<void> {
  const tableSel = el("select", { "aria-label": "What to trace" }) as HTMLSelectElement;
  for (const [k, t] of Object.entries(st.tables as Record<string, any>)) tableSel.append(el("option", { value: k }, `${t.label} (${k})`));
  const key = el("input", { "aria-label": "Customer or deal" }) as HTMLInputElement;
  const setHint = () => { key.placeholder = st.tables[tableSel.value].key; };
  tableSel.addEventListener("change", setHint);
  setHint();
  const out = el("div", {});
  const go = async () => {
    out.replaceChildren(el("p", { class: "muted" }, "Tracing..."));
    lastTrace = await clearledger.trace(tableSel.value, key.value);
    renderTrace(out, lastTrace);
  };
  key.addEventListener("keydown", (e) => { if (e.key === "Enter") void go(); });
  const db = st.dsn.source === "none"
    ? el("div", { class: "warn" }, el("strong", {}, "No database connected."), "Trace reads a ClearLedger warehouse. Connect your own in Settings, or look at the recorded example, which is the simulated company's data, not yours.")
    : el("p", { class: "muted" }, `Database: ${st.dsn.shown} (from ${st.dsn.source === "environment" ? "CLEARLEDGER_DSN" : st.dsn.source === "keychain" ? "the keychain" : "this session only"}).`);
  view.replaceChildren(el("h1", {}, "Trace a number"),
    el("p", { class: "muted" }, "Pick a finance number and get the invoices, payments and orders behind it, each with its Odoo id and version, checked to add up."),
    db, el("div", { class: "actions" }, tableSel, key, button("Trace", "primary", () => void go()), button("Show the recorded example", "", async () => renderTrace(out, (lastTrace = await clearledger.traceRecorded())))), out);
  if (lastTrace) renderTrace(out, lastTrace);
}

// ---------- Change check ----------

function renderChange(out: HTMLElement, r: any): void {
  if (!r.ok) {
    out.replaceChildren(errBox(r.error), el("div", { class: "actions" }, button("Show the recorded example", "", async () => renderChange(out, (lastChange = await clearledger.changeRecorded(st.changes[0].id))))));
    return;
  }
  const a = r.value;
  const v = a.verdict;
  const rec = a.origin === "recorded";
  out.replaceChildren(
    el("div", { class: `banner ${rec ? "recorded" : ""}` }, rec ? label("RECORDED") : label("LIVE"), a.note),
    el("div", { class: `card ${rec ? "recorded" : ""}` },
      el("div", { class: "verdict" }, v.block ? label("BLOCK") : label(v.severity.toUpperCase(), v.severity === "warn" ? "STUB" : "PASS"), v.block ? "Blocked: do not make this change without sign-off." : v.severity === "warn" ? "Allowed with a warning." : "Allowed."),
      el("div", { class: "line" }, `Risk: ${Number.isFinite(v.risk) ? v.risk : "?"} out of 100`),
      el("div", { class: "line" }, el("strong", {}, "Who must sign off: "), v.signOff.length ? v.signOff.join(", ") : "nobody named"),
      el("div", { class: "line" }, el("strong", {}, "Who to tell: "), v.notify.length ? v.notify.join(", ") : "nobody named"),
      el("h2", {}, "What it would break"),
      ...(v.affected.length ? v.affected.map((x: any) => el("div", { class: "line" }, `${x.asset}`, el("span", { class: "muted" }, `  tier ${x.tier ?? "unset"}, owner ${x.owner ?? "none"}`))) : [el("p", { class: "muted" }, "Nothing downstream.")]),
      v.constraints.length ? el("div", {}, el("h2", {}, "Rules it breaks"), ...v.constraints.map((c: string) => el("div", { class: "line" }, c))) : null,
      el("p", { class: "muted" }, v.summary)),
    el("p", { class: "src" }, `Command: ${a.command}. Verdict ${v.verdictId}.`),
  );
}

async function changeScreen(): Promise<void> {
  const sel = el("select", { "aria-label": "Change" }) as HTMLSelectElement;
  for (const c of st.changes) sel.append(el("option", { value: c.id }, c.title));
  const out = el("div", {});
  const go = async () => {
    out.replaceChildren(el("p", { class: "muted" }, "Checking (dbt parse, changelens sync, changelens preflight; can take a minute)..."));
    lastChange = await clearledger.change(sel.value);
    renderChange(out, lastChange);
  };
  view.replaceChildren(el("h1", {}, "Change check"),
    el("p", { class: "muted" }, "Before someone changes a finance table, ChangeLens says what the change would break, how risky it is, and who must sign off. If ChangeLens is not installed, the app shows the recorded example."),
    el("div", { class: "actions" }, sel, button("Check this change", "primary", () => void go()), button("Show the recorded example", "", async () => renderChange(out, (lastChange = await clearledger.changeRecorded(sel.value))))), out);
  if (lastChange) renderChange(out, lastChange);
}

// ---------- Readiness ----------

async function readinessScreen(): Promise<void> {
  view.replaceChildren(el("h1", {}, "Readiness"), el("p", { class: "muted" }, "Looking at this machine..."));
  const items = await clearledger.readiness();
  const rows = items.map((i: any) => el("div", { class: "card" },
    el("div", {}, el("span", { class: `status-${i.status}` }, i.status === "ok" ? "Found" : i.status === "missing" ? "Missing" : "Optional"), "  ", el("strong", {}, i.name)),
    el("div", { class: "line" }, i.found), el("div", { class: "muted" }, `Needed for: ${i.needFor}`),
    i.fix && i.status !== "ok" ? el("div", {}, el("strong", {}, "Fix"), el("pre", { class: "mono" }, i.fix)) : null));
  const missing = items.filter((i: any) => i.status === "missing").length;
  view.replaceChildren(el("h1", {}, "Readiness"), el("p", {}, missing ? `${missing} of ${items.length} things are missing. The screens still work: what cannot run here is shown from a labelled recording.` : `All ${items.length} found.`), ...rows,
    el("div", { class: "actions" }, button("Check again", "", () => void readinessScreen())));
}

// ---------- Settings ----------

async function settingsScreen(): Promise<void> {
  await loadState();
  const py = st.python;
  const dd = st.dataDir;
  const dsnIn = el("input", { type: "password", placeholder: "host=... dbname=warehouse user=... (or postgresql://...)", "aria-label": "Database address", autocomplete: "off", style: "width:min(520px,100%)" }) as HTMLInputElement;
  const remember = el("input", { type: "checkbox", id: "remember", style: "width:auto" }) as HTMLInputElement;
  if (!st.keychain) remember.disabled = true;
  const msg = el("p", { class: "err" }, settingsMsg);
  settingsMsg = "";
  const after = async (r: any) => { if (r && r.ok === false && r.error) settingsMsg = r.error.message; await settingsScreen(); };
  view.replaceChildren(el("h1", {}, "Settings"),
    el("div", { class: "card" }, el("h2", {}, "ClearLedger folder"),
      el("div", { class: "line" }, dd.value ? `${dd.value} (${dd.source})` : "Not set", dd.value && !dd.valid ? el("span", { class: "err" }, "  not a ClearLedger checkout") : null),
      el("p", { class: "muted" }, "The checkout of the ClearLedger repository: the app runs the core from there. CLEARLEDGER_HOME wins over the saved choice."),
      el("div", { class: "actions" }, button("Choose folder", "", async () => { await clearledger.chooseSetting("dataDir"); await settingsScreen(); }), button("Clear", "", async () => { await clearledger.clearSetting("dataDir"); await settingsScreen(); }))),
    el("div", { class: "card" }, el("h2", {}, "Python"),
      el("div", { class: "line" }, py.value ? `${py.value}, Python ${py.version} (${py.source})` : "No usable Python found"),
      py.help ? el("pre", { class: "mono" }, py.help) : null,
      el("p", { class: "muted" }, `Needs Python ${st.minPython} or newer. Order: CLEARLEDGER_PYTHON, the saved choice, the ClearLedger folder's .venv, then python3.13 to python3 on PATH.`),
      el("div", { class: "actions" }, button("Choose Python", "", async () => { await clearledger.chooseSetting("python"); await settingsScreen(); }), button("Clear", "", async () => { await clearledger.clearSetting("python"); await settingsScreen(); }))),
    el("div", { class: "card" }, el("h2", {}, "Connect your own database"),
      el("div", { class: "line" }, st.dsn.source === "none" ? "No database connected." : `${st.dsn.shown} (from ${st.dsn.source === "environment" ? "CLEARLEDGER_DSN" : st.dsn.source === "keychain" ? "the keychain" : "this session only"})`),
      el("p", { class: "muted" }, st.keychain
        ? "The address is kept for this session only, unless you tick the box: then it is encrypted with a key held in the macOS Keychain. It is never written in plain text. CLEARLEDGER_DSN in the app's environment wins over both."
        : "The keychain is not available here, so the address is kept in memory for this session only. CLEARLEDGER_DSN in the app's environment wins."),
      el("div", { class: "actions" }, dsnIn), el("div", { class: "actions" }, remember, el("label", { for: "remember" }, "Remember it in the keychain")),
      el("div", { class: "actions" }, button("Connect", "primary", async () => { const r = await clearledger.setDsn(dsnIn.value, remember.checked); dsnIn.value = ""; await after(r); }), button("Forget", "", async () => { await clearledger.clearDsn(); await settingsScreen(); })), msg));
}

// ---------- routing ----------

async function route(): Promise<void> {
  await loadState();
  const h = location.hash.replace(/^#/, "") || "/";
  for (const a of Array.from(document.querySelectorAll("nav a"))) a.classList.toggle("current", (a as HTMLAnchorElement).getAttribute("href") === `#${h}`);
  if (h === "/trace") return traceScreen();
  if (h === "/change") return changeScreen();
  if (h === "/readiness") return readinessScreen();
  if (h === "/settings") return settingsScreen();
  return demoScreen();
}

window.addEventListener("hashchange", () => void route());
void route();
