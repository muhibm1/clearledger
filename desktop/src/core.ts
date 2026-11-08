// The adapter between the app and the ClearLedger core. Pure Node, no Electron imports, so tests drive it with
// fake command output. The app talks to the core only by running its commands as child processes, with a
// timeout, and reads JSON where the command offers it.
import { spawn as nodeSpawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import { accessSync, constants, existsSync, statSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

// ---------- typed results ----------

export type ErrorKind = "not-installed" | "failed" | "bad-output" | "timeout" | "missing-dependency" | "no-database";
export interface AppError { kind: ErrorKind; message: string; fix?: string }
export type Result<T> = { ok: true; value: T } | { ok: false; error: AppError };

export interface RunResult {
  code: number | null;
  stdout: string;
  stderr: string;
  /** Set when the process could not run at all (ENOENT, EACCES) or was stopped for taking too long. */
  failure?: { kind: "spawn"; code: string; message: string } | { kind: "timeout"; ms: number };
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptions) => ChildProcess;

export const TIMEOUTS_MS = {
  probe: 15_000,
  demo: 120_000,
  walkthrough: 300_000,
  trace: 60_000,
  dbtParse: 180_000,
  changelensSync: 180_000,
  preflight: 120_000,
  dbCheck: 15_000,
} as const;
export type CommandName = keyof typeof TIMEOUTS_MS;

const MAX_OUTPUT = 8 * 1024 * 1024;

/** The Python the repository documents (README: "Needs Python 3.11"). */
export const MIN_PYTHON: [number, number] = [3, 11];

const fail = (kind: ErrorKind, message: string, fix?: string): Result<never> => ({ ok: false, error: fix ? { kind, message, fix } : { kind, message } });

// ---------- running a command ----------

export interface RunOptions { env: NodeJS.ProcessEnv; cwd?: string; timeoutMs: number; spawn?: SpawnFn }

/** Runs `cmd args` with no shell. Never rejects: a failure to run comes back in `failure`. */
export function runCommand(cmd: string, args: string[], opts: RunOptions): Promise<RunResult> {
  const doSpawn = opts.spawn ?? (nodeSpawn as SpawnFn);
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timer: NodeJS.Timeout | undefined;
    const done = (r: RunResult) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve(r);
    };
    let child: ChildProcess;
    try {
      child = doSpawn(cmd, args, { env: opts.env, cwd: opts.cwd, shell: false, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    } catch (e) {
      const err = e as NodeJS.ErrnoException;
      resolve({ code: null, stdout, stderr, failure: { kind: "spawn", code: err.code ?? "UNKNOWN", message: err.message } });
      return;
    }
    timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch { /* already gone */ }
      done({ code: null, stdout, stderr, failure: { kind: "timeout", ms: opts.timeoutMs } });
    }, opts.timeoutMs);
    child.stdout?.on("data", (d: Buffer) => { if (stdout.length < MAX_OUTPUT) stdout += d.toString("utf8"); });
    child.stderr?.on("data", (d: Buffer) => { stderr = (stderr + d.toString("utf8")).slice(-20_000); });
    child.on("error", (e: NodeJS.ErrnoException) => done({ code: null, stdout, stderr, failure: { kind: "spawn", code: e.code ?? "UNKNOWN", message: e.message } }));
    child.on("close", (code) => done({ code, stdout, stderr }));
  });
}

export function lastLines(text: string, n = 6): string {
  return text.trim().split(/\r?\n/).slice(-n).join("\n");
}

/** The exact command, for display and for a person to run in a terminal. */
export function commandText(cmd: string, args: string[]): string {
  const quote = (s: string) => (/^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);
  return [cmd, ...args].map(quote).join(" ");
}

/** Turns a failed run into a typed error. Python import errors become missing-dependency with the fix. */
export function runError(run: RunResult, what: string, dataDir?: string): AppError | null {
  if (run.failure?.kind === "timeout") return { kind: "timeout", message: `${what} took longer than ${Math.round(run.failure.ms / 1000)} seconds and was stopped.` };
  if (run.failure?.kind === "spawn") {
    if (run.failure.code === "ENOENT") return { kind: "not-installed", message: `${what} could not start: the program was not found.` };
    return { kind: "failed", message: `${what} could not start: ${run.failure.message}` };
  }
  if (run.code === 0) return null;
  const mod = /ModuleNotFoundError: No module named '([^']+)'/.exec(run.stderr);
  if (mod) {
    return { kind: "missing-dependency", message: `${what} needs the Python package "${mod[1]}", which this Python does not have.`, fix: installDepsCommand(dataDir) };
  }
  const tail = lastLines(run.stderr || run.stdout);
  return { kind: "failed", message: `${what} exited with code ${run.code}.${tail ? "\n" + tail : ""}` };
}

// ---------- finding things on this machine ----------

function isExecutable(p: string): boolean {
  try {
    if (!statSync(p).isFile()) return false;
    accessSync(p, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** PATH of the app's process plus the usual install folders: apps opened from Finder do not get the shell PATH. */
export function searchDirs(env: NodeJS.ProcessEnv, home: string = os.homedir()): string[] {
  const dirs = (env.PATH ?? "").split(path.delimiter).filter(Boolean);
  dirs.push("/opt/homebrew/bin", "/usr/local/bin", "/Library/Frameworks/Python.framework/Versions/Current/bin", path.join(home, ".local", "bin"), "/usr/bin");
  return [...new Set(dirs)];
}

export function findOnPath(name: string, env: NodeJS.ProcessEnv, exe: (p: string) => boolean = isExecutable, home?: string): string | null {
  for (const dir of searchDirs(env, home)) {
    const p = path.join(dir, name);
    if (exe(p)) return p;
  }
  return null;
}

export interface Saved { python?: string; dataDir?: string; dsnEncrypted?: string }
export type Source = "environment" | "saved setting" | "ClearLedger folder" | "PATH" | "next to the app" | "none";
export interface Found { value: string | null; source: Source }

/** True when `dir` looks like a ClearLedger checkout: it has the engagement demo and the CLI. */
export function isClearLedgerDir(dir: string | null | undefined, exists: (p: string) => boolean = existsSync): boolean {
  if (!dir) return false;
  return exists(path.join(dir, "engagement", "__main__.py")) && exists(path.join(dir, "clearledger", "cli.py"));
}

/** The ClearLedger folder: CLEARLEDGER_HOME, else the saved setting, else the checkout this app was built from. */
export function resolveDataDir(o: { env: NodeJS.ProcessEnv; saved: Saved; besideApp?: string | null; exists?: (p: string) => boolean }): Found {
  const fromEnv = (o.env.CLEARLEDGER_HOME ?? "").trim();
  if (fromEnv) return { value: fromEnv, source: "environment" };
  const fromSaved = (o.saved.dataDir ?? "").trim();
  if (fromSaved) return { value: fromSaved, source: "saved setting" };
  if (o.besideApp && isClearLedgerDir(o.besideApp, o.exists)) return { value: o.besideApp, source: "next to the app" };
  return { value: null, source: "none" };
}

/** Python candidates, best first: CLEARLEDGER_PYTHON, the saved setting, the ClearLedger folder's .venv, then
 * python3.13 down to python3 on PATH. Each is probed for its version before use. */
export function pythonCandidates(o: { env: NodeJS.ProcessEnv; saved: Saved; dataDir: string | null; exe?: (p: string) => boolean; home?: string }): Found[] {
  const exe = o.exe ?? isExecutable;
  const fromEnv = (o.env.CLEARLEDGER_PYTHON ?? "").trim();
  if (fromEnv) return [{ value: fromEnv, source: "environment" }];
  const fromSaved = (o.saved.python ?? "").trim();
  if (fromSaved) return [{ value: fromSaved, source: "saved setting" }];
  const out: Found[] = [];
  if (o.dataDir) {
    const venv = path.join(o.dataDir, ".venv", "bin", "python");
    if (exe(venv)) out.push({ value: venv, source: "ClearLedger folder" });
  }
  for (const name of ["python3.13", "python3.12", "python3.11", "python3"]) {
    const p = findOnPath(name, o.env, exe, o.home);
    if (p && !out.some((f) => f.value === p)) out.push({ value: p, source: "PATH" });
  }
  return out;
}

// ---------- probing Python ----------

/** One line of JSON about the interpreter and which of the core's packages it can import. Imports nothing heavy. */
export const PY_PROBE = [
  "import sys, json, os, importlib.util as u",
  "b = os.path.dirname(sys.executable)",
  "print(json.dumps({'version': list(sys.version_info[:3]), 'executable': sys.executable,",
  " 'psycopg': u.find_spec('psycopg') is not None,",
  " 'dbt': os.path.exists(os.path.join(b, 'dbt')), 'changelens': os.path.exists(os.path.join(b, 'changelens'))}))",
].join("\n");

export interface PythonInfo { path: string; source: Source; version: [number, number, number]; executable: string; psycopg: boolean; dbtBeside: boolean; changelensBeside: boolean; meetsMinimum: boolean }

export const versionText = (v: number[]): string => v.join(".");
export const meetsMinimum = (v: number[]): boolean => v[0]! > MIN_PYTHON[0] || (v[0] === MIN_PYTHON[0] && v[1]! >= MIN_PYTHON[1]);

export function parsePythonProbe(run: RunResult, candidate: Found): Result<PythonInfo> {
  const err = runError(run, `${candidate.value} (Python check)`);
  if (err) return { ok: false, error: err };
  let j: any;
  try {
    j = JSON.parse(run.stdout.trim().split(/\r?\n/).pop() ?? "");
  } catch {
    return fail("bad-output", `${candidate.value} did not answer the Python check with JSON.`);
  }
  const v = j?.version;
  if (!Array.isArray(v) || v.length < 2 || !v.every((n: unknown) => typeof n === "number")) return fail("bad-output", `${candidate.value} did not report a version.`);
  const nums = v as number[];
  const version: [number, number, number] = [nums[0]!, nums[1]!, nums[2] ?? 0];
  return {
    ok: true,
    value: {
      path: candidate.value ?? "", source: candidate.source, version, executable: String(j.executable ?? candidate.value),
      psycopg: j.psycopg === true, dbtBeside: j.dbt === true, changelensBeside: j.changelens === true, meetsMinimum: meetsMinimum(version),
    },
  };
}

export function installPythonHelp(dataDir: string | null): string {
  return `Install Python ${MIN_PYTHON.join(".")} or newer from https://www.python.org/downloads/macos/ , then create the core's environment:\n${installDepsCommand(dataDir)}\nOr point the app at a Python ${MIN_PYTHON.join(".")}+ in Settings, or set CLEARLEDGER_PYTHON.`;
}

export function installDepsCommand(dataDir?: string | null): string {
  const dir = dataDir ? commandText("cd", [dataDir]) : "cd /path/to/clearledger";
  return `${dir} && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`;
}

export interface PythonChoice { chosen: PythonInfo | null; tried: Array<{ candidate: Found; result: Result<PythonInfo> }>; help: string | null }

/** Probes candidates in order and picks the first that meets the minimum. A candidate the person named (environment
 * or saved setting) is the only one tried: the app never quietly swaps in another Python. */
export async function choosePython(candidates: Found[], probe: (c: Found) => Promise<RunResult>, dataDir: string | null): Promise<PythonChoice> {
  const tried: PythonChoice["tried"] = [];
  for (const c of candidates) {
    if (!c.value) continue;
    const r = parsePythonProbe(await probe(c), c);
    tried.push({ candidate: c, result: r });
    if (r.ok && r.value.meetsMinimum) return { chosen: r.value, tried, help: null };
  }
  const found = tried.filter((t) => t.result.ok).map((t) => (t.result.ok ? `${t.candidate.value} is ${versionText(t.result.value.version)}` : ""));
  const head = candidates.length === 0 ? "No Python was found." : found.length ? `No Python ${MIN_PYTHON.join(".")} or newer was found (${found.join("; ")}).` : "No Python that runs was found.";
  return { chosen: null, tried, help: `${head}\n${installPythonHelp(dataDir)}` };
}

// ---------- the child's environment ----------

/** The core's environment: the person's own, with CLEARLEDGER_DSN set to the database chosen in the app (environment,
 * keychain or this session) and removed when none is chosen or the command needs none. With none, a core command that
 * reads a warehouse falls back to its own default (the local socket in /tmp, database warehouse) and labels the step. */
export function childEnv(base: NodeJS.ProcessEnv, dsn: string | null): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...base, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1", NO_COLOR: "1", COLUMNS: "200" };
  delete env.CLEARLEDGER_DSN;
  if (dsn) env.CLEARLEDGER_DSN = dsn;
  return env;
}

// ---------- the database address ----------

export type DsnSource = "environment" | "keychain" | "memory" | "none";
export interface DsnChoice { dsn: string | null; source: DsnSource }

/** CLEARLEDGER_DSN in the app's environment wins, then one saved in the OS keychain, then one held in memory. */
export function chooseDsn(o: { env: NodeJS.ProcessEnv; keychain: string | null; memory: string | null }): DsnChoice {
  const e = (o.env.CLEARLEDGER_DSN ?? "").trim();
  if (e) return { dsn: e, source: "environment" };
  if (o.keychain && o.keychain.trim()) return { dsn: o.keychain.trim(), source: "keychain" };
  if (o.memory && o.memory.trim()) return { dsn: o.memory.trim(), source: "memory" };
  return { dsn: null, source: "none" };
}

/** A DSN safe to show: passwords removed from key=value and URL forms. */
export function redactDsn(dsn: string | null): string {
  if (!dsn) return "";
  return dsn
    .replace(/(password\s*=\s*)('(?:[^'\\]|\\.)*'|\S+)/gi, "$1****")
    .replace(/(postgres(?:ql)?:\/\/[^:/@\s]+):[^@\s]*@/gi, "$1:****@")
    .replace(/([?&]password=)[^&\s]*/gi, "$1****");
}

/** Python that connects with a 3 second timeout and prints JSON. The DSN comes from the environment, never argv. */
export const DB_CHECK = [
  "import json, os",
  "try:",
  "    import psycopg",
  "except ImportError:",
  "    print(json.dumps({'ok': False, 'why': 'psycopg is not installed'})); raise SystemExit(0)",
  "try:",
  "    with psycopg.connect(os.environ['CLEARLEDGER_DSN'], connect_timeout=3) as c:",
  "        n = c.execute(\"select count(*) from information_schema.tables where table_schema = 'marts'\").fetchone()[0]",
  "    print(json.dumps({'ok': True, 'marts_tables': n}))",
  "except Exception as e:",
  "    print(json.dumps({'ok': False, 'why': type(e).__name__ + ': ' + str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__}))",
].join("\n");

export interface DbCheck { reachable: boolean; martsTables?: number; why?: string }

export function parseDbCheck(run: RunResult): Result<DbCheck> {
  const err = runError(run, "The database check");
  if (err) return { ok: false, error: err };
  try {
    const j = JSON.parse(run.stdout.trim().split(/\r?\n/).pop() ?? "");
    if (j.ok === true) return { ok: true, value: { reachable: true, martsTables: typeof j.marts_tables === "number" ? j.marts_tables : undefined } };
    return { ok: true, value: { reachable: false, why: String(j.why ?? "unknown") } };
  } catch {
    return fail("bad-output", "The database check did not print JSON.");
  }
}

// ---------- the demo ----------

export type StepLabel = "LIVE" | "STUB" | "RECORDED" | "SKIPPED";
const LABELS: StepLabel[] = ["LIVE", "STUB", "RECORDED", "SKIPPED"];
export interface WalkStep { step: number; name: string; mode: StepLabel; reason: string; ok: boolean; text?: string; lines: string[] }
export interface Seeded { name: string; status: "PASS" | "FAIL" | "SKIP"; detail: string }
export interface DemoView {
  /** "live": the core ran now on this machine. "recorded": the app's own recording, not run here. */
  origin: "live" | "recorded";
  seeded: Seeded[];
  recordedRuns: string[];
  scorecard: string[];
  walkthrough: WalkStep[];
  summary: string;
  banner: string;
}

export const demoArgs = (jsonOut: string): string[] => ["-m", "engagement", "--json", jsonOut];
export const walkthroughArgs = (jsonOut: string): string[] => ["-m", "engagement", "walkthrough", "--json", jsonOut];

/** Reads the `N. name   [LABEL]` headings the walkthrough prints, with the reason line under each. */
export function parseWalkthroughText(text: string): WalkStep[] {
  const steps: WalkStep[] = [];
  const lines = text.split(/\r?\n/);
  let cur: WalkStep | null = null;
  for (const line of lines) {
    const h = /^(\d+)\.\s+(.+?)\s+\[(LIVE|STUB|RECORDED|SKIPPED)\]\s*$/.exec(line);
    if (h) {
      cur = { step: Number(h[1]), name: h[2]!, mode: h[3] as StepLabel, reason: "", ok: true, lines: [] };
      steps.push(cur);
      continue;
    }
    if (!cur) continue;
    if (/^Summary:/.test(line)) { cur = null; continue; }
    if (/^-{10,}$/.test(line)) continue;
    const r = new RegExp(`^\\s{2}${cur.mode}: (.*)$`).exec(line);
    if (r && !cur.reason) { cur.reason = r[1]!; continue; }
    if (/^\s+FAILED\s*$/.test(line)) { cur.ok = false; continue; }
    cur.lines.push(line.replace(/^ {2}/, ""));
  }
  for (const s of steps) {
    while (s.lines.length && !s.lines[0]!.trim()) s.lines.shift();
    while (s.lines.length && !s.lines[s.lines.length - 1]!.trim()) s.lines.pop();
  }
  return steps;
}

/** The walkthrough's --json file. Labels outside the four the core documents are refused, never guessed. */
export function parseWalkthroughJson(raw: string): Result<WalkStep[]> {
  let j: any;
  try { j = JSON.parse(raw); } catch { return fail("bad-output", "The walkthrough's JSON file could not be read."); }
  if (!j || !Array.isArray(j.steps)) return fail("bad-output", "The walkthrough's JSON file has no steps.");
  const out: WalkStep[] = [];
  for (const s of j.steps) {
    if (!LABELS.includes(s?.mode)) return fail("bad-output", `The walkthrough printed a step label the app does not know: ${String(s?.mode)}.`);
    const res = s.result && typeof s.result === "object" ? s.result : {};
    const lines: string[] = [];
    if (typeof res.summary === "string") lines.push(res.summary);
    if (Array.isArray(res.observed)) for (const o of res.observed) lines.push(String(o));
    if (typeof res.answer !== "undefined") lines.push(`answer: ${String(res.answer)}`);
    out.push({ step: Number(s.step), name: String(s.name ?? ""), mode: s.mode, reason: String(s.reason ?? ""), ok: s.ok !== false, text: typeof res.text === "string" ? res.text : undefined, lines });
  }
  return { ok: true, value: out };
}

/** Reads the demo's printed text: PASS/FAIL/SKIP lines with their detail, the recorded section and the scorecard. */
export function parseDemoText(text: string): { seeded: Seeded[]; recordedRuns: string[]; scorecard: string[] } {
  const seeded: Seeded[] = [];
  const recordedRuns: string[] = [];
  const scorecard: string[] = [];
  let section = 0;
  let last: Seeded | null = null;
  for (const line of text.split(/\r?\n/)) {
    const sec = /^(\d)\.\s/.exec(line);
    if (sec) { section = Number(sec[1]); last = null; continue; }
    if (/^-{10,}$/.test(line) || !line.trim()) continue;
    if (section === 1) {
      const m = /^\s{2}(PASS|FAIL|SKIP)\s{2}(.*)$/.exec(line);
      if (m) { last = { status: m[1] as Seeded["status"], name: m[2]!, detail: "" }; seeded.push(last); }
      else if (last) last.detail = (last.detail ? last.detail + " " : "") + line.trim();
    } else if (section === 2) recordedRuns.push(line.replace(/^ {2}/, ""));
    else if (section === 3 && /^\s{2}\S/.test(line)) scorecard.push(line.trim());
  }
  return { seeded, recordedRuns, scorecard };
}

export const RECORDED_BANNER = "RECORDED: this is the app's recording of the demo, not a run on this machine and not your data.";
export const LIVE_BANNER = "Ran now on this machine. The client and its customers are invented; every label below says what actually ran.";

/** Builds what the Demo screen shows from the two runs' text and JSON. JSON wins where both exist. */
export function buildDemoView(o: { origin: "live" | "recorded"; demoText: string; walkText: string; walkJson?: string | null }): Result<DemoView> {
  const d = parseDemoText(o.demoText);
  if (d.seeded.length === 0) return fail("bad-output", "The demo printed no seeded failures.");
  let walk = parseWalkthroughText(o.walkText);
  if (o.walkJson) {
    const j = parseWalkthroughJson(o.walkJson);
    if (!j.ok) return j;
    // Keep the printed detail lines (the JSON holds the same facts less readably), take mode and ok from JSON.
    walk = j.value.map((s) => {
      const t = walk.find((w) => w.step === s.step);
      // A step with a text block (the trace) shows that block; its printed lines are the same text.
      if (s.text) return s;
      return { ...s, lines: t && t.lines.length ? t.lines : s.lines };
    });
  }
  if (walk.length === 0) return fail("bad-output", "The walkthrough printed no labelled steps.");
  const sum = /^Summary: (.*)$/m.exec(o.walkText)?.[1] ?? walk.map((s) => `${s.step} ${s.mode}`).join(", ");
  return { ok: true, value: { origin: o.origin, seeded: d.seeded, recordedRuns: d.recordedRuns, scorecard: d.scorecard, walkthrough: walk, summary: sum, banner: o.origin === "recorded" ? RECORDED_BANNER : LIVE_BANNER } };
}

// ---------- trace ----------

export const TRACE_TABLES = {
  receivables_aging: { label: "What a customer owes, by how late", key: "Customer name (or part of it)" },
  revenue_by_customer: { label: "What a customer was billed and has paid", key: "Customer name (or part of it)" },
  deal_to_cash: { label: "A won deal, from order to cash", key: "Deal id (a number)" },
} as const;
export type TraceTable = keyof typeof TRACE_TABLES;
export const isTraceTable = (t: unknown): t is TraceTable => typeof t === "string" && Object.prototype.hasOwnProperty.call(TRACE_TABLES, t);

/** `clearledger trace`, run as the module so no install step is needed. The key goes after `--`. */
export const traceArgs = (table: TraceTable, key: string): string[] => ["-m", "clearledger.cli", "trace", table, "--", key];
export const traceDisplay = (table: TraceTable, key: string): string => commandText("clearledger", ["trace", table, key]);

export interface TraceTableBlock { title: string; columns: string[]; rows: string[][] }
export interface TraceCheck { label: string; claimed: string; sum: string; ok: boolean }
export interface TraceView { headline: string; notes: string[]; tables: TraceTableBlock[]; checks: TraceCheck[]; addsUp: boolean; raw: string }

/** Splits a fixed-width row by the column starts the dashed rule under the header gives. */
function splitFixed(line: string, starts: number[]): string[] {
  return starts.map((s, i) => line.slice(s, i + 1 < starts.length ? starts[i + 1] : undefined).trim());
}

export function parseTrace(text: string): Result<TraceView> {
  const lines = text.replace(/\s+$/, "").split(/\r?\n/);
  const headline = (lines[0] ?? "").trim();
  if (!headline) return fail("bad-output", "clearledger trace printed nothing.");
  const notes: string[] = [];
  const tables: TraceTableBlock[] = [];
  const checks: TraceCheck[] = [];
  let i = 1;
  while (i < lines.length) {
    const line = lines[i]!;
    const chk = /^\s{2}(\w[\w ]*): (\S+) = sum of the records above \((\S+)\) (OK|DOES NOT ADD UP)\s*$/.exec(line);
    if (chk) { checks.push({ label: chk[1]!, claimed: chk[2]!, sum: chk[3]!, ok: chk[4] === "OK" }); i++; continue; }
    if (/^\S/.test(line) && i + 1 < lines.length) {
      const title = line.trim();
      const next = lines[i + 1]!;
      if (/^\s{2}\(none\)\s*$/.test(next)) { tables.push({ title, columns: [], rows: [] }); i += 2; continue; }
      const rule = lines[i + 2] ?? "";
      if (/^\s{2}-+(\s{2}-+)*\s*$/.test(rule)) {
        const starts: number[] = [];
        const re = /-+/g;
        let m: RegExpExecArray | null;
        while ((m = re.exec(rule))) starts.push(m.index);
        const columns = splitFixed(next, starts);
        const rows: string[][] = [];
        i += 3;
        while (i < lines.length && lines[i]!.trim() && !/= sum of the records above/.test(lines[i]!)) { rows.push(splitFixed(lines[i]!, starts)); i++; }
        tables.push({ title, columns, rows });
        continue;
      }
    }
    if (line.trim()) notes.push(line.trim());
    i++;
  }
  if (checks.length === 0) return fail("bad-output", "clearledger trace printed no checks, so the app cannot say whether the records add up.");
  return { ok: true, value: { headline, notes, tables, checks, addsUp: checks.every((c) => c.ok), raw: text } };
}

export function traceResult(run: RunResult, dataDir: string | null): Result<TraceView> {
  const err = runError(run, "clearledger trace", dataDir ?? undefined);
  if (err) {
    // The core exits with a plain sentence for a name that matches no customer or several.
    const plain = lastLines(run.stderr, 1);
    if (run.code === 1 && /matches \d+ customers|no won deal with id/.test(plain)) return fail("failed", plain);
    if (/OperationalError|could not connect|Connection refused|timeout expired/i.test(run.stderr)) {
      return fail("no-database", "The database did not answer. Check the address in Settings, and that it is a warehouse built by ClearLedger's dbt models.\n" + lastLines(run.stderr, 2));
    }
    if (/UndefinedTable|relation "marts\./.test(run.stderr)) return fail("failed", "The database answered but has no ClearLedger finance tables (marts). Point the app at a warehouse built by this repository's dbt models.");
    return { ok: false, error: err };
  }
  return parseTrace(run.stdout);
}

// ---------- change check (ChangeLens) ----------

export const CHANGES = [
  {
    id: "drop-late-over-90",
    title: "Drop the \"more than 90 days late\" column from the aging table",
    intent: "drop the late_over_90 column from receivables_aging",
    recorded: "preflight-drop-column.txt",
  },
] as const;
export type ChangeId = (typeof CHANGES)[number]["id"];

export interface Affected { asset: string; tier: number | null; owner: string | null }
export interface Verdict {
  severity: "inform" | "warn" | "block";
  block: boolean;
  risk: number;
  summary: string;
  affected: Affected[];
  signOff: string[];
  notify: string[];
  constraints: string[];
  verdictId: string;
}

export const preflightArgs = (intent: string): string[] => ["preflight", "--intent", intent, "--format", "json", "--no-record"];
export const dbtParseArgs = (): string[] => ["parse", "--profiles-dir", ".", "--quiet"];
export const syncArgs = (): string[] => ["sync"];

const SEVERITIES = ["inform", "warn", "block"] as const;

/** `changelens preflight --format json`. Sign-off comes from the get_approval checks, never inferred. */
export function parsePreflightJson(raw: string): Result<Verdict> {
  let v: any;
  try { v = JSON.parse(raw); } catch { return fail("bad-output", "changelens preflight did not print JSON."); }
  if (!v || !SEVERITIES.includes(v.severity)) return fail("bad-output", "changelens preflight printed no severity.");
  const checks: any[] = Array.isArray(v.checks) ? v.checks : [];
  const signOff = checks.filter((c) => c?.kind === "get_approval").flatMap((c) => (Array.isArray(c.from) ? c.from.map(String) : []));
  const notify = checks.filter((c) => c?.kind === "notify_owner" && typeof c.owner === "string").map((c) => c.owner as string);
  const affected: Affected[] = (Array.isArray(v.affected) ? v.affected : []).map((a: any) => ({ asset: String(a?.asset ?? ""), tier: typeof a?.tier === "number" ? a.tier : null, owner: typeof a?.owner === "string" ? a.owner : null }));
  const constraints = (Array.isArray(v.constraints) ? v.constraints : []).filter((c: any) => c?.status === "fires").map((c: any) => `${c.id} ${c.kind} (${c.severity}): ${c.detail}`);
  return {
    ok: true,
    value: { severity: v.severity, block: v.severity === "block", risk: Number(v.risk?.score ?? NaN), summary: String(v.summary ?? ""), affected, signOff: [...new Set(signOff)], notify: [...new Set(notify)], constraints, verdictId: String(v.verdict_id ?? "") },
  };
}

/** The text form changelens prints, used for the recorded example. Lines may be wrapped at about 110 columns. */
export function parsePreflightText(text: string): Result<Verdict> {
  const lines = text.split(/\r?\n/);
  const head = /^(INFORM|WARN|BLOCK)\s+risk (\d+)\/100\s+(\S+)/.exec(lines[0] ?? "");
  if (!head) return fail("bad-output", "The preflight text does not start with a verdict line.");
  const sections = new Set(["change", "unresolved", "constraints", "history", "checks"]);
  const isSection = (l: string) => sections.has(l.trim()) || /^affected \(\d+\)$/.test(l.trim());
  let i = 1;
  const summary: string[] = [];
  while (i < lines.length && !isSection(lines[i]!)) summary.push(lines[i++]!.trim());
  const blocks: Record<string, string[]> = {};
  let cur = "";
  for (; i < lines.length; i++) {
    const l = lines[i]!;
    if (isSection(l)) { cur = l.trim().startsWith("affected") ? "affected" : l.trim(); blocks[cur] = []; continue; }
    if (/^risk: /.test(l)) { cur = ""; continue; }
    if (!cur) continue;
    // A line that does not start with two spaces continues the one above (the terminal wrapped it).
    if (/^\s{2}\S/.test(l) || !blocks[cur]!.length) blocks[cur]!.push(l.trim());
    else blocks[cur]![blocks[cur]!.length - 1] += " " + l.trim();
  }
  const affected: Affected[] = (blocks.affected ?? []).map((l) => {
    const m = /^(\S+) tier (\d+|unset), ([^,]+),/.exec(l);
    return { asset: m?.[1] ?? l.split(" ")[0]!, tier: m && m[2] !== "unset" ? Number(m[2]) : null, owner: m && m[3] !== "no owner" ? m[3]! : null };
  });
  const signOff: string[] = [];
  const notify: string[] = [];
  for (const l of blocks.checks ?? []) {
    const ap = /^get_approval\s+\{'from': \[([^\]]*)\]/.exec(l);
    if (ap) for (const s of ap[1]!.split(",")) { const t = s.trim().replace(/^'|'$/g, ""); if (t) signOff.push(t); }
    const no = /^notify_owner\s+\{'owner': '([^']+)'/.exec(l);
    if (no) notify.push(no[1]!);
  }
  const severity = head[1]!.toLowerCase() as Verdict["severity"];
  return {
    ok: true,
    value: { severity, block: severity === "block", risk: Number(head[2]), summary: summary.join(" ").replace(/\s+/g, " ").trim(), affected, signOff, notify, constraints: blocks.constraints ?? [], verdictId: head[3]! },
  };
}

/** Where dbt and changelens are: next to the chosen Python first (the core's own environment), then PATH. */
export function findTool(name: "dbt" | "changelens", python: string | null, env: NodeJS.ProcessEnv, exe: (p: string) => boolean = isExecutable): string | null {
  if (python) {
    const beside = path.join(path.dirname(python), name);
    if (exe(beside)) return beside;
  }
  return findOnPath(name, env, exe);
}

export const CHANGELENS_HELP = (dataDir: string | null) =>
  `ChangeLens and dbt are installed with the core's requirements:\n${installDepsCommand(dataDir)}`;

// ---------- readiness ----------

export type Status = "ok" | "missing" | "optional";
export interface ReadinessItem { name: string; status: Status; found: string; needFor: string; fix?: string }

export interface Facts {
  dataDir: Found;
  dataDirValid: boolean;
  python: PythonChoice;
  dbt: string | null;
  changelens: string | null;
  dsn: DsnChoice;
  db: DbCheck | null;
  odoo: { url: string; reachable: boolean } | null;
}

/** A plain list of what the app found and what is missing, each missing item with the exact fix. */
export function buildReadiness(f: Facts): ReadinessItem[] {
  const dir = f.dataDir.value;
  const items: ReadinessItem[] = [];
  items.push(f.dataDirValid
    ? { name: "ClearLedger folder", status: "ok", found: `${dir} (${f.dataDir.source})`, needFor: "everything the app runs" }
    : { name: "ClearLedger folder", status: "missing", found: dir ? `${dir} is not a ClearLedger checkout` : "not set", needFor: "everything the app runs", fix: "git clone https://github.com/muhibm1/clearledger, then choose that folder in Settings (or set CLEARLEDGER_HOME)" });
  const py = f.python.chosen;
  items.push(py
    ? { name: `Python ${MIN_PYTHON.join(".")} or newer`, status: "ok", found: `${versionText(py.version)} at ${py.path} (${py.source})`, needFor: "the demo, trace and readiness checks" }
    : { name: `Python ${MIN_PYTHON.join(".")} or newer`, status: "missing", found: f.python.help?.split("\n")[0] ?? "not found", needFor: "the demo, trace and readiness checks (without it the Demo screen shows the RECORDED run)", fix: installPythonHelp(dir) });
  items.push(py?.psycopg
    ? { name: "psycopg (Postgres driver for Python)", status: "ok", found: `importable by ${py.path}`, needFor: "Trace" }
    : { name: "psycopg (Postgres driver for Python)", status: "missing", found: py ? `not importable by ${py.path}` : "no Python to check", needFor: "Trace", fix: installDepsCommand(dir) });
  items.push(f.dbt
    ? { name: "dbt", status: "ok", found: f.dbt, needFor: "Change check (dbt parse reads the models)" }
    : { name: "dbt", status: "missing", found: "not found next to the Python or on PATH", needFor: "Change check (without it the screen shows the RECORDED example)", fix: installDepsCommand(dir) });
  items.push(f.changelens
    ? { name: "ChangeLens", status: "ok", found: f.changelens, needFor: "Change check" }
    : { name: "ChangeLens", status: "missing", found: "not found next to the Python or on PATH", needFor: "Change check (without it the screen shows the RECORDED example)", fix: installDepsCommand(dir) });
  if (f.dsn.source === "none") {
    items.push({ name: "Warehouse database (Postgres)", status: "missing", found: "no database address set", needFor: "Trace on your own data", fix: "Settings: Connect your own database, or start the app with CLEARLEDGER_DSN set. A warehouse comes from README.md, 'Run it' (Postgres 16, Odoo 17, python -m clearledger.demo)." });
  } else if (f.db?.reachable) {
    const marts = f.db.martsTables ?? 0;
    items.push(marts > 0
      ? { name: "Warehouse database (Postgres)", status: "ok", found: `${redactDsn(f.dsn.dsn)} answered, ${marts} finance tables (${f.dsn.source})`, needFor: "Trace on your own data" }
      : { name: "Warehouse database (Postgres)", status: "missing", found: `${redactDsn(f.dsn.dsn)} answered but has no finance tables (marts schema)`, needFor: "Trace on your own data", fix: "Build the warehouse first: README.md, 'Run it' (python -m clearledger.demo)." });
  } else {
    items.push({ name: "Warehouse database (Postgres)", status: "missing", found: `${redactDsn(f.dsn.dsn)} did not answer${f.db?.why ? `: ${f.db.why}` : ""}`, needFor: "Trace on your own data", fix: "Start Postgres and check the address in Settings. Postgres 16 is what CI uses." });
  }
  items.push(f.odoo?.reachable
    ? { name: "Odoo 17", status: "ok", found: `${f.odoo.url} answered`, needFor: "loading fresh data (not used by this app)" }
    : { name: "Odoo 17", status: "optional", found: f.odoo ? `${f.odoo.url} did not answer` : "not checked", needFor: "loading fresh data only; the app's screens do not need it", fix: dir ? `${commandText("cd", [dir])} && scripts/odoo-local.sh install && scripts/odoo-local.sh reset` : "scripts/odoo-local.sh install && scripts/odoo-local.sh reset (in the ClearLedger folder)" });
  return items;
}

// ---------- who may call the main process ----------

/** True only for the app's own page: the main frame, loaded from a local file under the app's renderer folder. */
export function isTrustedSender(frame: { url: string; parent?: unknown } | null | undefined, rendererIndexUrl: string): boolean {
  if (!frame || frame.parent) return false;
  return frame.url === rendererIndexUrl || frame.url.startsWith(rendererIndexUrl + "#");
}
