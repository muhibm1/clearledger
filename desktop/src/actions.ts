// What each screen does, as plain functions over the core adapter. No Electron imports: main.ts wires these to the
// page, smoke.ts and the tests call them directly with a fake runner.
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import {
  buildDemoView, buildReadiness, CHANGES, CHANGELENS_HELP, childEnv, choosePython, commandText, DB_CHECK, dbtParseArgs, demoArgs, findTool,
  isClearLedgerDir, parseDbCheck, parsePreflightJson, parsePreflightText, parseTrace, preflightArgs, PY_PROBE, pythonCandidates, resolveDataDir,
  runCommand, runError, syncArgs, TIMEOUTS_MS, traceArgs, traceDisplay, traceResult, walkthroughArgs,
  type ChangeId, type CommandName, type DbCheck, type DemoView, type DsnChoice, type Found, type PythonChoice, type ReadinessItem, type Result,
  type RunResult, type Saved, type TraceTable, type TraceView, type Verdict,
} from "./core";

/** Runs one command with the timeout for its kind. Tests pass a fake. */
export type Runner = (cmd: string, args: string[], o: { env: NodeJS.ProcessEnv; cwd?: string; kind: CommandName }) => Promise<RunResult>;
export const realRunner: Runner = (cmd, args, o) => runCommand(cmd, args, { env: o.env, cwd: o.cwd, timeoutMs: TIMEOUTS_MS[o.kind] });

export interface Paths {
  /** The checkout the app was built from, when run from source; null in the packaged app. */
  besideApp: string | null;
  /** The app's own recordings of the demo. */
  recordedDir: string;
  /** The repository's recorded examples (docs/examples). */
  examplesDir: string;
}

export interface Ctx { env: NodeJS.ProcessEnv; saved: Saved; dsn: DsnChoice; paths: Paths; run: Runner; exe?: (p: string) => boolean }

export interface Located { dataDir: Found; dataDirValid: boolean; python: PythonChoice }

export async function locate(c: Ctx): Promise<Located> {
  const dataDir = resolveDataDir({ env: c.env, saved: c.saved, besideApp: c.paths.besideApp });
  const valid = isClearLedgerDir(dataDir.value);
  const candidates = pythonCandidates({ env: c.env, saved: c.saved, dataDir: valid ? dataDir.value : null, exe: c.exe });
  const python = await choosePython(candidates, (cand) => c.run(cand.value!, ["-c", PY_PROBE], { env: childEnv(c.env, null), kind: "probe" }), valid ? dataDir.value : null);
  return { dataDir, dataDirValid: valid, python };
}

// ---------- Demo ----------

export interface DemoAnswer { view: DemoView; why: string | null; commands: string[] }

export function recordedDemo(paths: Paths, why: string): Result<DemoAnswer> {
  try {
    const read = (n: string) => readFileSync(path.join(paths.recordedDir, n), "utf8");
    const manifest = JSON.parse(read("manifest.json"));
    const v = buildDemoView({ origin: "recorded", demoText: read("demo.txt"), walkText: read("walkthrough.txt"), walkJson: read("walkthrough.json") });
    if (!v.ok) return v;
    const note = `Recorded on ${manifest.recorded_on} at commit ${manifest.commit} with Python ${manifest.python}. ${manifest.environment}`;
    return { ok: true, value: { view: v.value, why: `${why}\n${note}`, commands: manifest.commands ?? [] } };
  } catch (e) {
    return { ok: false, error: { kind: "bad-output", message: `The app's recording could not be read: ${e instanceof Error ? e.message : String(e)}` } };
  }
}

/** Runs `python -m engagement` and its walkthrough now. Without a usable Python or folder, the recording, labelled. */
export async function runDemo(c: Ctx): Promise<Result<DemoAnswer>> {
  const loc = await locate(c);
  if (!loc.dataDirValid) return recordedDemo(c.paths, "No ClearLedger folder is set, so nothing ran here. Choose it in Settings.");
  if (!loc.python.chosen) return recordedDemo(c.paths, loc.python.help ?? "No suitable Python.");
  const py = loc.python.chosen.path;
  const dir = loc.dataDir.value!;
  const tmp = mkdtempSync(path.join(os.tmpdir(), "clearledger-demo-"));
  try {
    const env = childEnv(c.env, c.dsn.dsn);
    const dj = path.join(tmp, "demo.json");
    const wj = path.join(tmp, "walkthrough.json");
    const d = await c.run(py, demoArgs(dj), { env, cwd: dir, kind: "demo" });
    // The demo exits 1 when a seeded failure is not handled: that is a result to show, not an error.
    const de = d.code === 1 && d.stdout.includes("Scorecard") ? null : runError(d, "python -m engagement", dir);
    if (de) return { ok: false, error: de };
    const w = await c.run(py, walkthroughArgs(wj), { env, cwd: dir, kind: "walkthrough" });
    const we = w.code === 1 && /^Summary:/m.test(w.stdout) ? null : runError(w, "python -m engagement walkthrough", dir);
    if (we) return { ok: false, error: we };
    let walkJson: string | null = null;
    try { walkJson = readFileSync(wj, "utf8"); } catch { walkJson = null; }
    const v = buildDemoView({ origin: "live", demoText: d.stdout, walkText: w.stdout, walkJson });
    if (!v.ok) return v;
    return { ok: true, value: { view: v.value, why: null, commands: [commandText(py, ["-m", "engagement"]), commandText(py, ["-m", "engagement", "walkthrough"])] } };
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------- Trace ----------

export interface TraceAnswer { origin: "live" | "recorded"; view: TraceView; command: string; note: string }

export function recordedTrace(paths: Paths): Result<TraceAnswer> {
  try {
    const t = parseTrace(readFileSync(path.join(paths.examplesDir, "trace-northwind.txt"), "utf8"));
    if (!t.ok) return t;
    return { ok: true, value: { origin: "recorded", view: t.value, command: traceDisplay("revenue_by_customer", "northwind"), note: "RECORDED: docs/examples/trace-northwind.txt, a trace of the simulated company's Northwind Logistics. Not your data." } };
  } catch (e) {
    return { ok: false, error: { kind: "bad-output", message: `The recorded trace could not be read: ${e instanceof Error ? e.message : String(e)}` } };
  }
}

export async function runTrace(c: Ctx, table: TraceTable, key: string): Promise<Result<TraceAnswer>> {
  if (!c.dsn.dsn) return { ok: false, error: { kind: "no-database", message: "No database is connected. Connect your own in Settings, or look at the recorded example." } };
  const loc = await locate(c);
  if (!loc.dataDirValid) return { ok: false, error: { kind: "not-installed", message: "No ClearLedger folder is set. Choose it in Settings." } };
  if (!loc.python.chosen) return { ok: false, error: { kind: "not-installed", message: loc.python.help ?? "No suitable Python." } };
  const r = await c.run(loc.python.chosen.path, traceArgs(table, key), { env: childEnv(c.env, c.dsn.dsn), cwd: loc.dataDir.value!, kind: "trace" });
  const t = traceResult(r, loc.dataDir.value);
  if (!t.ok) return t;
  return { ok: true, value: { origin: "live", view: t.value, command: traceDisplay(table, key), note: `Ran now on the database from ${c.dsn.source === "environment" ? "CLEARLEDGER_DSN" : c.dsn.source === "keychain" ? "the keychain" : "this session"}.` } };
}

// ---------- Change check ----------

export interface ChangeAnswer { origin: "live" | "recorded"; verdict: Verdict; command: string; note: string }

const changeById = (id: ChangeId) => CHANGES.find((x) => x.id === id)!;

export function recordedChange(paths: Paths, id: ChangeId, why: string): Result<ChangeAnswer> {
  const ch = changeById(id);
  try {
    const v = parsePreflightText(readFileSync(path.join(paths.examplesDir, ch.recorded), "utf8"));
    if (!v.ok) return v;
    return { ok: true, value: { origin: "recorded", verdict: v.value, command: commandText("changelens", ["preflight", "--intent", ch.intent]), note: `RECORDED: docs/examples/${ch.recorded}, an earlier ChangeLens run on this repository's dbt project. ${why}` } };
  } catch (e) {
    return { ok: false, error: { kind: "bad-output", message: `The recorded example could not be read: ${e instanceof Error ? e.message : String(e)}` } };
  }
}

/** dbt parse, changelens sync, then changelens preflight --format json, the same steps the engagement demo runs. */
export async function runChangeCheck(c: Ctx, id: ChangeId): Promise<Result<ChangeAnswer>> {
  const ch = changeById(id);
  const loc = await locate(c);
  if (!loc.dataDirValid) return recordedChange(c.paths, id, "No ClearLedger folder is set, so nothing ran here.");
  const py = loc.python.chosen?.path ?? null;
  const dbt = findTool("dbt", py, c.env, c.exe);
  const cl = findTool("changelens", py, c.env, c.exe);
  if (!dbt || !cl) return recordedChange(c.paths, id, `${!cl ? "ChangeLens" : "dbt"} is not installed, so nothing ran here. ${CHANGELENS_HELP(loc.dataDir.value)}`);
  const dir = loc.dataDir.value!;
  const env = childEnv(c.env, null);
  const p = await c.run(dbt, dbtParseArgs(), { env, cwd: path.join(dir, "dbt"), kind: "dbtParse" });
  const pe = runError(p, "dbt parse", dir);
  if (pe) return { ok: false, error: pe };
  const s = await c.run(cl, syncArgs(), { env, cwd: dir, kind: "changelensSync" });
  const se = runError(s, "changelens sync", dir);
  if (se) return { ok: false, error: se };
  const args = preflightArgs(ch.intent);
  const r = await c.run(cl, args, { env, cwd: dir, kind: "preflight" });
  const re = runError(r, "changelens preflight", dir);
  if (re) return { ok: false, error: re };
  // Typer prints the JSON; any constraint-file warnings come after it on their own lines.
  const json = r.stdout.slice(0, r.stdout.lastIndexOf("}") + 1);
  const v = parsePreflightJson(json);
  if (!v.ok) return v;
  return { ok: true, value: { origin: "live", verdict: v.value, command: commandText("changelens", args), note: "Ran now: ChangeLens on this ClearLedger folder's dbt project. No database is used." } };
}

// ---------- Readiness ----------

export type OdooProbe = (url: string) => Promise<boolean>;

export const tcpProbe: OdooProbe = (url) =>
  new Promise((resolve) => {
    let u: URL;
    try { u = new URL(url); } catch { resolve(false); return; }
    const port = Number(u.port || (u.protocol === "https:" ? 443 : 80));
    const sock = net.connect({ host: u.hostname, port, timeout: 1500 });
    const done = (ok: boolean) => { sock.destroy(); resolve(ok); };
    sock.once("connect", () => done(true));
    sock.once("timeout", () => done(false));
    sock.once("error", () => done(false));
  });

export async function readiness(c: Ctx, odooProbe: OdooProbe = tcpProbe): Promise<ReadinessItem[]> {
  const loc = await locate(c);
  const py = loc.python.chosen;
  let db: DbCheck | null = null;
  if (c.dsn.dsn && py) {
    const r = parseDbCheck(await c.run(py.path, ["-c", DB_CHECK], { env: childEnv(c.env, c.dsn.dsn), kind: "dbCheck" }));
    db = r.ok ? r.value : { reachable: false, why: r.error.message };
  } else if (c.dsn.dsn) db = { reachable: false, why: "no Python to check it with" };
  const odooUrl = (c.env.ODOO_URL ?? "").trim() || "http://localhost:8069";
  return buildReadiness({
    dataDir: loc.dataDir, dataDirValid: loc.dataDirValid, python: loc.python,
    dbt: findTool("dbt", py?.path ?? null, c.env, c.exe), changelens: findTool("changelens", py?.path ?? null, c.env, c.exe),
    dsn: c.dsn, db, odoo: { url: odooUrl, reachable: await odooProbe(odooUrl) },
  });
}
