import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import * as c from "../build/core.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const fx = (n) => readFileSync(path.join(here, "fixtures", n), "utf8");
const rec = (n) => readFileSync(path.join(here, "..", "recorded", n), "utf8");
const example = (n) => readFileSync(path.join(repo, "docs", "examples", n), "utf8");
const FAKE = path.join(here, "fake-core.mjs");
const fake = (mode, extra = {}, timeoutMs = 10000, args = ["x"]) => c.runCommand(FAKE, args, { env: { ...process.env, FAKE_MODE: mode, ...extra }, timeoutMs });
const okRun = (stdout, code = 0, stderr = "") => ({ code, stdout, stderr });

// ---------- running a command: success, failure, missing tool, timeout ----------

test("runCommand: success returns stdout and code 0", async () => {
  const r = await fake("fixture", { FAKE_FIXTURE: path.join(here, "fixtures", "trace-aging.txt") });
  assert.equal(r.code, 0);
  assert.equal(r.failure, undefined);
  assert.match(r.stdout, /^Larkspur Foods: owes/);
  assert.equal(c.runError(r, "x"), null);
});

test("runCommand: failure keeps the exit code and the stderr tail", async () => {
  const r = await fake("fail");
  assert.equal(r.code, 2);
  const e = c.runError(r, "clearledger trace");
  assert.equal(e.kind, "failed");
  assert.match(e.message, /exited with code 2/);
  assert.match(e.message, /Traceback: boom/);
});

test("runCommand: a missing tool is not-installed, never a crash", async () => {
  const r = await c.runCommand("/nonexistent/clearledger-python", ["-V"], { env: process.env, timeoutMs: 5000 });
  assert.equal(r.failure.kind, "spawn");
  assert.equal(r.failure.code, "ENOENT");
  assert.equal(c.runError(r, "python").kind, "not-installed");
});

test("runCommand: a hung command is stopped at the timeout", async () => {
  const t0 = Date.now();
  const r = await fake("hang", {}, 400);
  assert.equal(r.failure.kind, "timeout");
  assert.ok(Date.now() - t0 < 5000);
  const e = c.runError(r, "python -m engagement");
  assert.equal(e.kind, "timeout");
  assert.match(e.message, /was stopped/);
});

test("runCommand: arguments go through without a shell", async () => {
  const r = await fake("args", {}, 5000, ["trace", "receivables_aging", "--", "O'Brien; rm -rf /"]);
  const j = JSON.parse(r.stdout);
  assert.deepEqual(j.argv, ["trace", "receivables_aging", "--", "O'Brien; rm -rf /"]);
});

test("runError: a missing Python package becomes missing-dependency with the install command", async () => {
  const r = await fake("no-psycopg");
  const e = c.runError(r, "clearledger trace", "/tmp/cl");
  assert.equal(e.kind, "missing-dependency");
  assert.match(e.message, /"psycopg"/);
  assert.equal(e.fix, "cd /tmp/cl && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt");
});

// ---------- finding Python ----------

const probe = (v, extra = {}) => okRun(JSON.stringify({ version: v, executable: "/x/python", psycopg: false, dbt: false, changelens: false, ...extra }) + "\n");

test("parsePythonProbe: reads the version and the packages", () => {
  const r = c.parsePythonProbe(probe([3, 11, 18], { psycopg: true }), { value: "/x/python", source: "PATH" });
  assert.ok(r.ok);
  assert.deepEqual(r.value.version, [3, 11, 18]);
  assert.equal(r.value.psycopg, true);
  assert.equal(r.value.meetsMinimum, true);
  assert.equal(c.parsePythonProbe(okRun("Python 3.9.6"), { value: "p", source: "PATH" }).error.kind, "bad-output");
});

test("choosePython: the system 3.9 is refused with the exact install command; a 3.11 is chosen", async () => {
  const cands = [{ value: "/usr/bin/python3", source: "PATH" }, { value: "/opt/py311", source: "PATH" }];
  const only39 = await c.choosePython([cands[0]], async () => probe([3, 9, 6]), "/Users/me/clearledger");
  assert.equal(only39.chosen, null);
  assert.match(only39.help, /No Python 3\.11 or newer was found \(\/usr\/bin\/python3 is 3\.9\.6\)/);
  assert.match(only39.help, /cd \/Users\/me\/clearledger && python3\.11 -m venv \.venv && \.venv\/bin\/pip install -r requirements\.txt/);
  assert.match(only39.help, /CLEARLEDGER_PYTHON/);
  const both = await c.choosePython(cands, async (cand) => probe(cand.value === "/opt/py311" ? [3, 11, 2] : [3, 9, 6]), null);
  assert.equal(both.chosen.path, "/opt/py311");
  assert.equal(both.tried.length, 2);
  const none = await c.choosePython([], async () => probe([3, 12, 0]), null);
  assert.match(none.help, /^No Python was found\./);
});

test("pythonCandidates: CLEARLEDGER_PYTHON alone wins; otherwise the folder's .venv, then PATH", () => {
  const exe = (p) => ["/repo/.venv/bin/python", "/bin1/python3.12", "/bin1/python3"].includes(p);
  assert.deepEqual(c.pythonCandidates({ env: { CLEARLEDGER_PYTHON: "/my/py" }, saved: { python: "/saved" }, dataDir: "/repo", exe }), [{ value: "/my/py", source: "environment" }]);
  assert.deepEqual(c.pythonCandidates({ env: {}, saved: { python: "/saved" }, dataDir: "/repo", exe }), [{ value: "/saved", source: "saved setting" }]);
  const list = c.pythonCandidates({ env: { PATH: "/bin1" }, saved: {}, dataDir: "/repo", exe, home: "/h" });
  assert.deepEqual(list.map((f) => f.value), ["/repo/.venv/bin/python", "/bin1/python3.12", "/bin1/python3"]);
  assert.equal(list[0].source, "ClearLedger folder");
});

test("resolveDataDir and isClearLedgerDir", () => {
  assert.equal(c.isClearLedgerDir(repo), true);
  assert.equal(c.isClearLedgerDir(here), false);
  assert.deepEqual(c.resolveDataDir({ env: { CLEARLEDGER_HOME: "/e" }, saved: { dataDir: "/s" }, besideApp: repo }), { value: "/e", source: "environment" });
  assert.deepEqual(c.resolveDataDir({ env: {}, saved: {}, besideApp: repo }), { value: repo, source: "next to the app" });
  assert.deepEqual(c.resolveDataDir({ env: {}, saved: {}, besideApp: here }), { value: null, source: "none" });
});

// ---------- the database address ----------

test("chooseDsn: environment, then keychain, then memory", () => {
  assert.deepEqual(c.chooseDsn({ env: { CLEARLEDGER_DSN: "host=e" }, keychain: "host=k", memory: "host=m" }), { dsn: "host=e", source: "environment" });
  assert.deepEqual(c.chooseDsn({ env: {}, keychain: "host=k", memory: "host=m" }), { dsn: "host=k", source: "keychain" });
  assert.deepEqual(c.chooseDsn({ env: { CLEARLEDGER_DSN: " " }, keychain: null, memory: "host=m" }), { dsn: "host=m", source: "memory" });
  assert.deepEqual(c.chooseDsn({ env: {}, keychain: null, memory: null }), { dsn: null, source: "none" });
});

test("redactDsn: no password is ever shown", () => {
  assert.equal(c.redactDsn("host=db dbname=warehouse user=fin password=s3cret"), "host=db dbname=warehouse user=fin password=****");
  assert.equal(c.redactDsn("host=db password='a b c' user=x"), "host=db password=**** user=x");
  assert.equal(c.redactDsn("postgresql://fin:s3cret@db:5432/warehouse"), "postgresql://fin:****@db:5432/warehouse");
  assert.equal(c.redactDsn("postgres://db/warehouse?user=x&password=s3cret"), "postgres://db/warehouse?user=x&password=****");
  assert.equal(c.redactDsn(null), "");
});

test("childEnv: the chosen DSN only, never one the app was not given", () => {
  const e = c.childEnv({ PATH: "/bin", CLEARLEDGER_DSN: "host=old" }, null);
  assert.equal(e.CLEARLEDGER_DSN, undefined);
  assert.equal(c.childEnv({ PATH: "/bin" }, "host=new").CLEARLEDGER_DSN, "host=new");
  assert.equal(e.PYTHONDONTWRITEBYTECODE, "1");
});

test("parseDbCheck: reachable, unreachable, garbage", () => {
  assert.deepEqual(c.parseDbCheck(okRun('{"ok": true, "marts_tables": 4}\n')).value, { reachable: true, martsTables: 4 });
  assert.deepEqual(c.parseDbCheck(okRun('{"ok": false, "why": "OperationalError: refused"}')).value, { reachable: false, why: "OperationalError: refused" });
  assert.equal(c.parseDbCheck(okRun("nope")).error.kind, "bad-output");
});

// ---------- label parsing ----------

test("parseWalkthroughText: the recorded run has its four labels as the core printed them", () => {
  const steps = c.parseWalkthroughText(rec("walkthrough.txt"));
  assert.deepEqual(steps.map((s) => [s.step, s.mode]), [[1, "RECORDED"], [2, "SKIPPED"], [3, "SKIPPED"], [4, "RECORDED"]]);
  assert.match(steps[0].reason, /^the warehouse at CLEARLEDGER_DSN did not connect/);
  assert.match(steps[0].lines.join("\n"), /Northwind Logistics: billed 49,490\.00/);
  assert.ok(steps[3].lines.some((l) => /cost_cap/.test(l)));
  assert.ok(steps.every((s) => s.ok));
});

test("parseWalkthroughText: LIVE, STUB, SKIPPED and a FAILED step", () => {
  const steps = c.parseWalkthroughText(fx("walkthrough-live.txt"));
  assert.deepEqual(steps.map((s) => s.mode), ["LIVE", "STUB", "LIVE", "SKIPPED"]);
  assert.equal(steps[2].ok, false);
  assert.equal(steps[0].ok, true);
  assert.match(steps[1].reason, /^scripted model/);
  assert.ok(steps[1].lines.some((l) => l.startsWith("question :")));
  assert.ok(!steps[3].lines.some((l) => l.startsWith("Summary")));
});

test("parseWalkthroughJson: reads the file; refuses a label it does not know", () => {
  const r = c.parseWalkthroughJson(rec("walkthrough.json"));
  assert.ok(r.ok);
  assert.equal(r.value[0].mode, "RECORDED");
  assert.match(r.value[0].text, /^Northwind Logistics/);
  const bad = c.parseWalkthroughJson(JSON.stringify({ steps: [{ step: 1, name: "x", mode: "MAYBE", reason: "" }] }));
  assert.equal(bad.error.kind, "bad-output");
  assert.match(bad.error.message, /MAYBE/);
  assert.equal(c.parseWalkthroughJson("{").error.kind, "bad-output");
});

test("parseDemoText: seeded failures with their detail, the recorded section, the scorecard", () => {
  const d = c.parseDemoText(rec("demo.txt"));
  assert.equal(d.seeded.length, 12);
  assert.equal(d.seeded.filter((s) => s.status === "PASS").length, 11);
  assert.equal(d.seeded[11].status, "SKIP");
  assert.match(d.seeded[0].detail, /held as instruction_in_invoice_text/);
  assert.ok(d.recordedRuns.some((l) => /Pipeline drills, 2026-10-27: 5 of 5/.test(l)));
  assert.ok(d.scorecard.some((l) => /11 of 11/.test(l)));
});

test("buildDemoView: a recording is labelled RECORDED and never as a run here", () => {
  const v = c.buildDemoView({ origin: "recorded", demoText: rec("demo.txt"), walkText: rec("walkthrough.txt"), walkJson: rec("walkthrough.json") });
  assert.ok(v.ok);
  assert.equal(v.value.origin, "recorded");
  assert.equal(v.value.banner, c.RECORDED_BANNER);
  assert.match(v.value.banner, /^RECORDED: .*not your data/);
  assert.equal(v.value.summary, "1 RECORDED, 2 SKIPPED, 3 SKIPPED, 4 RECORDED.");
  assert.equal(c.buildDemoView({ origin: "live", demoText: "nothing", walkText: "" }).ok, false);
});

// ---------- trace ----------

test("parseTrace: the recorded Northwind trace, two tables, three checks that add up", () => {
  const t = c.parseTrace(example("trace-northwind.txt"));
  assert.ok(t.ok);
  assert.match(t.value.headline, /^Northwind Logistics: billed 49,490\.00/);
  assert.deepEqual(t.value.notes, ["merged from Odoo partners [41, 71]"]);
  assert.deepEqual(t.value.tables.map((x) => [x.title, x.rows.length]), [["Invoices", 3], ["Payments", 2]]);
  assert.equal(t.value.tables[0].columns[0], "invoice");
  assert.equal(t.value.tables[0].rows[0][0], "INV/2026/00036");
  assert.equal(t.value.tables[0].rows[0][10], "account.move/83@2026-10-27T08:33:00");
  assert.equal(t.value.tables[1].rows[1][4], "109");
  assert.deepEqual(t.value.checks.map((x) => x.label), ["billed", "open", "paid"]);
  assert.equal(t.value.addsUp, true);
});

test("parseTrace: receivables aging; and a trace that does not add up says so", () => {
  const a = c.parseTrace(fx("trace-aging.txt"));
  assert.equal(a.value.tables[0].title, "Open invoices");
  assert.equal(a.value.tables[0].rows[0][2], "119");
  assert.equal(a.value.addsUp, true);
  const b = c.parseTrace(fx("trace-broken.txt"));
  assert.equal(b.value.addsUp, false);
  assert.deepEqual(b.value.tables.map((x) => [x.title, x.rows.length]), [["Orders", 0], ["Invoices", 1]]);
  assert.deepEqual(b.value.checks.map((x) => x.ok), [false, false]);
  assert.equal(c.parseTrace("Something\nwith no checks").error.kind, "bad-output");
});

test("traceResult: ambiguous name, no database, missing driver, no finance tables", () => {
  assert.match(c.traceResult(okRun("", 1, "'a' matches 3 customers in receivables_aging: A1, A2, A3\n"), null).error.message, /^'a' matches 3 customers/);
  assert.equal(c.traceResult(okRun("", 1, "psycopg.OperationalError: connection failed: Connection refused"), null).error.kind, "no-database");
  assert.equal(c.traceResult(okRun("", 1, "ModuleNotFoundError: No module named 'psycopg'"), "/r").error.kind, "missing-dependency");
  assert.match(c.traceResult(okRun("", 1, 'psycopg.errors.UndefinedTable: relation "marts.receivables_aging" does not exist'), null).error.message, /no ClearLedger finance tables/);
  assert.ok(c.traceResult(okRun(fx("trace-aging.txt")), null).ok);
});

test("traceArgs: the key goes after -- so a leading dash is never an option", () => {
  assert.deepEqual(c.traceArgs("receivables_aging", "-x"), ["-m", "clearledger.cli", "trace", "receivables_aging", "--", "-x"]);
  assert.equal(c.traceDisplay("revenue_by_customer", "north wind"), "clearledger trace revenue_by_customer 'north wind'");
  assert.equal(c.isTraceTable("deal_to_cash"), true);
  assert.equal(c.isTraceTable("__proto__"), false);
});

// ---------- change check ----------

test("parsePreflightJson: BLOCK, risk, who signs off, who to tell, only fired rules", () => {
  const v = c.parsePreflightJson(fx("preflight.json"));
  assert.ok(v.ok);
  assert.equal(v.value.block, true);
  assert.equal(v.value.risk, 72);
  assert.deepEqual(v.value.signOff, ["team/finance"]);
  assert.deepEqual(v.value.notify, ["Finance", "Revenue ops"]);
  assert.equal(v.value.constraints.length, 1);
  assert.match(v.value.constraints[0], /^CON-101/);
  assert.deepEqual(v.value.affected[2], { asset: "model:scratch", tier: null, owner: null });
  assert.equal(c.parsePreflightJson('{"severity": "maybe"}').error.kind, "bad-output");
  assert.equal(c.parsePreflightJson("not json").error.kind, "bad-output");
});

test("parsePreflightText: the recorded drop-column example, wrapped lines joined", () => {
  const v = c.parsePreflightText(example("preflight-drop-column.txt"));
  assert.ok(v.ok);
  assert.equal(v.value.severity, "block");
  assert.equal(v.value.risk, 72);
  assert.deepEqual(v.value.signOff, ["team/finance"]);
  assert.deepEqual(v.value.notify, ["Finance", "Revenue ops"]);
  assert.deepEqual(v.value.affected.map((a) => [a.asset, a.tier, a.owner]), [["report:collections_queue", 1, "Revenue ops"], ["report:month_end_close", 1, "Finance"]]);
  assert.match(v.value.constraints[0], /^CON-101 columns_locked \(block\): .* directly$/);
  assert.match(v.value.summary, /^Drop column on receivables_aging\.late_over_90 reaches 2 downstream assets/);
  const inform = c.parsePreflightText(example("preflight-overdue-invoices.txt"));
  assert.equal(inform.value.block, false);
  assert.equal(inform.value.severity, "inform");
  assert.deepEqual(inform.value.signOff, []);
  assert.equal(c.parsePreflightText("hello").error.kind, "bad-output");
});

test("preflightArgs asks for JSON and does not write to the ledger", () => {
  assert.deepEqual(c.preflightArgs("drop x"), ["preflight", "--intent", "drop x", "--format", "json", "--no-record"]);
});

// ---------- readiness ----------

const nothing = {
  dataDir: { value: null, source: "none" }, dataDirValid: false,
  python: { chosen: null, tried: [], help: "No Python 3.11 or newer was found (/usr/bin/python3 is 3.9.6).\nInstall..." },
  dbt: null, changelens: null, dsn: { dsn: null, source: "none" }, db: null, odoo: { url: "http://localhost:8069", reachable: false },
};

test("buildReadiness: a bare machine lists every gap with its fix", () => {
  const items = c.buildReadiness(nothing);
  assert.deepEqual(items.map((i) => i.name), ["ClearLedger folder", "Python 3.11 or newer", "psycopg (Postgres driver for Python)", "dbt", "ChangeLens", "Warehouse database (Postgres)", "Odoo 17"]);
  assert.ok(items.slice(0, 6).every((i) => i.status === "missing" && i.fix));
  assert.equal(items[6].status, "optional");
  assert.match(items[1].found, /3\.9\.6/);
  assert.match(items[1].fix, /python3\.11 -m venv \.venv/);
  assert.match(items[0].fix, /git clone https:\/\/github\.com\/muhibm1\/clearledger/);
});

test("buildReadiness: a ready machine, and a database that answers without finance tables", () => {
  const py = { path: "/r/.venv/bin/python", source: "ClearLedger folder", version: [3, 11, 18], executable: "/r/.venv/bin/python", psycopg: true, dbtBeside: true, changelensBeside: true, meetsMinimum: true };
  const ready = c.buildReadiness({ ...nothing, dataDir: { value: "/r", source: "saved setting" }, dataDirValid: true, python: { chosen: py, tried: [], help: null }, dbt: "/r/.venv/bin/dbt", changelens: "/r/.venv/bin/changelens", dsn: { dsn: "host=db password=x", source: "keychain" }, db: { reachable: true, martsTables: 4 }, odoo: { url: "http://localhost:8069", reachable: true } });
  assert.ok(ready.every((i) => i.status === "ok"));
  assert.match(ready[5].found, /password=\*\*\*\*/);
  assert.ok(!ready.some((i) => /password=x/.test(i.found)));
  const empty = c.buildReadiness({ ...nothing, dsn: { dsn: "host=db", source: "memory" }, db: { reachable: true, martsTables: 0 } });
  assert.equal(empty[5].status, "missing");
  assert.match(empty[5].found, /no finance tables/);
  const down = c.buildReadiness({ ...nothing, dsn: { dsn: "host=db", source: "memory" }, db: { reachable: false, why: "OperationalError: refused" } });
  assert.match(down[5].found, /did not answer: OperationalError/);
});

// ---------- misc ----------

test("commandText quotes what a shell would split", () => {
  assert.equal(c.commandText("changelens", ["preflight", "--intent", "drop the x column"]), "changelens preflight --intent 'drop the x column'");
  assert.equal(c.commandText("cd", ["/a b/it's"]), "cd '/a b/it'\\''s'");
});

test("isTrustedSender: only the app's own main frame", () => {
  const u = "file:///app/build/renderer/index.html";
  assert.equal(c.isTrustedSender({ url: u }, u), true);
  assert.equal(c.isTrustedSender({ url: u + "#/trace" }, u), true);
  assert.equal(c.isTrustedSender({ url: u, parent: {} }, u), false);
  assert.equal(c.isTrustedSender({ url: "https://example.com" }, u), false);
  assert.equal(c.isTrustedSender(null, u), false);
});
