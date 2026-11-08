// The screens' logic with a fake runner: what runs, with which arguments, and what the app falls back to.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import * as a from "../build/actions.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const paths = { besideApp: repo, recordedDir: path.join(here, "..", "recorded"), examplesDir: path.join(repo, "docs", "examples") };
const rec = (n) => readFileSync(path.join(paths.recordedDir, n), "utf8");
const ok = (stdout, code = 0, stderr = "") => ({ code, stdout, stderr });
const probe = (v, extra = {}) => ok(JSON.stringify({ version: v, executable: "/py", psycopg: true, dbt: false, changelens: false, ...extra }));

/** A fake machine: `files` are the executables that exist; `answer` maps a call to its output. */
function machine(files, answer) {
  const calls = [];
  const run = async (cmd, args, o) => {
    calls.push({ cmd, args, kind: o.kind, cwd: o.cwd, dsn: o.env.CLEARLEDGER_DSN ?? null });
    return answer(cmd, args, o);
  };
  return { calls, ctx: (extra = {}) => ({ env: { PATH: "/bin" }, saved: {}, dsn: { dsn: null, source: "none" }, paths, run, exe: (p) => files.includes(p), ...extra }) };
}

test("runDemo: with only the system Python 3.9 the app shows the RECORDED run and says how to fix it", async () => {
  const m = machine(["/bin/python3"], (cmd, args) => (args[0] === "-c" ? probe([3, 9, 6]) : ok("", 99)));
  const r = await a.runDemo(m.ctx());
  assert.ok(r.ok);
  assert.equal(r.value.view.origin, "recorded");
  assert.match(r.value.view.banner, /^RECORDED/);
  assert.match(r.value.why, /No Python 3\.11 or newer was found \(\/bin\/python3 is 3\.9\.6\)/);
  assert.match(r.value.why, /python3\.11 -m venv \.venv/);
  assert.match(r.value.why, /Recorded on 2026-11-08 at commit f4ac081/);
  assert.ok(m.calls.every((c) => c.kind === "probe"), "nothing but the probe ran");
});

test("runDemo: with a 3.11 it runs both commands in the ClearLedger folder and keeps the core's labels", async () => {
  const m = machine(["/bin/python3.11"], (cmd, args) => {
    if (args[0] === "-c") return probe([3, 11, 9]);
    if (args.includes("walkthrough")) return ok(readFileSync(path.join(here, "fixtures", "walkthrough-live.txt"), "utf8"), 1);
    return ok(rec("demo.txt"));
  });
  const r = await a.runDemo(m.ctx({ dsn: { dsn: "host=db", source: "memory" } }));
  assert.ok(r.ok, JSON.stringify(r.error));
  assert.equal(r.value.view.origin, "live");
  assert.deepEqual(r.value.view.walkthrough.map((s) => s.mode), ["LIVE", "STUB", "LIVE", "SKIPPED"]);
  assert.equal(r.value.view.walkthrough[2].ok, false, "a failed step stays failed");
  const runs = m.calls.filter((c) => c.kind !== "probe");
  assert.deepEqual(runs.map((c) => c.args.slice(0, 3)), [["-m", "engagement", "--json"], ["-m", "engagement", "walkthrough"]]);
  assert.ok(runs.every((c) => c.cwd === repo && c.dsn === "host=db"));
});

test("runDemo: a timeout is an error the page can show, with the recording one click away", async () => {
  const m = machine(["/bin/python3.11"], (cmd, args) => (args[0] === "-c" ? probe([3, 11, 9]) : { code: null, stdout: "", stderr: "", failure: { kind: "timeout", ms: 120000 } }));
  const r = await a.runDemo(m.ctx());
  assert.equal(r.ok, false);
  assert.equal(r.error.kind, "timeout");
});

test("runTrace: no database means no run", async () => {
  const m = machine(["/bin/python3.11"], () => probe([3, 11, 9]));
  const r = await a.runTrace(m.ctx(), "receivables_aging", "larkspur");
  assert.equal(r.error.kind, "no-database");
  assert.equal(m.calls.length, 0);
});

test("runTrace: runs clearledger trace with the chosen DSN and parses the records", async () => {
  const m = machine(["/bin/python3.11"], (cmd, args) => (args[0] === "-c" ? probe([3, 11, 9]) : ok(readFileSync(path.join(here, "fixtures", "trace-aging.txt"), "utf8"))));
  const r = await a.runTrace(m.ctx({ dsn: { dsn: "host=db password=x", source: "keychain" } }), "receivables_aging", "larkspur");
  assert.ok(r.ok);
  assert.equal(r.value.origin, "live");
  assert.equal(r.value.view.addsUp, true);
  assert.match(r.value.note, /keychain/);
  const t = m.calls.find((c) => c.kind === "trace");
  assert.deepEqual(t.args, ["-m", "clearledger.cli", "trace", "receivables_aging", "--", "larkspur"]);
  assert.equal(t.dsn, "host=db password=x");
});

test("recordedTrace: labelled RECORDED, not your data", () => {
  const r = a.recordedTrace(paths);
  assert.equal(r.value.origin, "recorded");
  assert.match(r.value.note, /^RECORDED: .*Not your data\./);
});

test("runChangeCheck: without ChangeLens it shows the recorded example and says why", async () => {
  const m = machine(["/bin/python3.11"], () => probe([3, 11, 9]));
  const r = await a.runChangeCheck(m.ctx(), "drop-late-over-90");
  assert.ok(r.ok);
  assert.equal(r.value.origin, "recorded");
  assert.equal(r.value.verdict.block, true);
  assert.match(r.value.note, /ChangeLens is not installed/);
  assert.ok(!m.calls.some((c) => c.kind === "preflight"));
});

test("runChangeCheck: with dbt and ChangeLens beside the Python it runs parse, sync, preflight --format json", async () => {
  const files = ["/v/bin/python3.11", "/v/bin/dbt", "/v/bin/changelens"];
  const m = machine(files, (cmd, args) => {
    if (args[0] === "-c") return probe([3, 11, 9]);
    if (args[0] === "preflight") return ok(readFileSync(path.join(here, "fixtures", "preflight.json"), "utf8") + "\n");
    return ok("");
  });
  const r = await a.runChangeCheck(m.ctx({ env: { PATH: "/v/bin" } }), "drop-late-over-90");
  assert.ok(r.ok, JSON.stringify(r.error));
  assert.equal(r.value.origin, "live");
  assert.deepEqual(r.value.verdict.signOff, ["team/finance"]);
  const steps = m.calls.filter((c) => c.kind !== "probe");
  assert.deepEqual(steps.map((c) => [c.cmd, c.args[0], c.cwd]), [["/v/bin/dbt", "parse", path.join(repo, "dbt")], ["/v/bin/changelens", "sync", repo], ["/v/bin/changelens", "preflight", repo]]);
  assert.ok(steps[2].args.includes("--no-record"));
  assert.ok(steps.every((c) => c.dsn === null), "the change check never gets a database");
});

test("runChangeCheck: a failing dbt parse is reported, not hidden behind the recording", async () => {
  const files = ["/v/bin/python3.11", "/v/bin/dbt", "/v/bin/changelens"];
  const m = machine(files, (cmd, args) => (args[0] === "-c" ? probe([3, 11, 9]) : args[0] === "parse" ? ok("", 2, "Compilation Error in model x") : ok("")));
  const r = await a.runChangeCheck(m.ctx({ env: { PATH: "/v/bin" } }), "drop-late-over-90");
  assert.equal(r.ok, false);
  assert.match(r.error.message, /dbt parse exited with code 2/);
});

test("readiness: probes Python, the database through Python, and Odoo; lists the gaps", async () => {
  const m = machine(["/bin/python3.11"], (cmd, args) => {
    if (args[0] === "-c" && args[1].includes("sys.version_info")) return probe([3, 11, 9], { psycopg: true });
    return ok('{"ok": false, "why": "OperationalError: connection refused"}');
  });
  const items = await a.readiness(m.ctx({ dsn: { dsn: "host=db password=s", source: "memory" } }), async () => false);
  const by = Object.fromEntries(items.map((i) => [i.name, i]));
  assert.equal(by["Python 3.11 or newer"].status, "ok");
  assert.equal(by["psycopg (Postgres driver for Python)"].status, "ok");
  assert.equal(by["ChangeLens"].status, "missing");
  assert.match(by["Warehouse database (Postgres)"].found, /password=\*\*\*\* did not answer: OperationalError/);
  const db = m.calls.find((c) => c.kind === "dbCheck");
  assert.equal(db.dsn, "host=db password=s");
  assert.ok(!db.args.join(" ").includes("password=s"), "the DSN is passed in the environment, not the arguments");
});
