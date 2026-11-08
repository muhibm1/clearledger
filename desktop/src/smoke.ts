// Headless checks, run by `electron . --smoke`. Opens no window. Runs the demo (live when a Python 3.11+ and the
// ClearLedger folder are found, else the recording), parses the recorded trace and change check, and builds the
// readiness list. It never connects to a database: the smoke runs with no DSN.
import { realRunner, readiness, recordedChange, recordedDemo, recordedTrace, runDemo, type Ctx, type Paths } from "./actions";
import type { Saved } from "./core";

export async function smoke(paths: Paths, saved: Saved, env: NodeJS.ProcessEnv): Promise<{ ok: boolean; summary: Record<string, unknown> }> {
  const c: Ctx = { env, saved, dsn: { dsn: null, source: "none" }, paths, run: realRunner };
  const rec = recordedDemo(paths, "smoke");
  const demo = await runDemo(c);
  const trace = recordedTrace(paths);
  const change = recordedChange(paths, "drop-late-over-90", "smoke");
  const ready = await readiness(c, async () => false);
  const labelsOk = (d: typeof demo) => d.ok && d.value.view.walkthrough.length === 4 && d.value.view.walkthrough.every((s) => ["LIVE", "STUB", "RECORDED", "SKIPPED"].includes(s.mode));
  const checks = {
    recordedDemoParses: rec.ok && rec.value.view.origin === "recorded" && rec.value.view.seeded.length >= 11 && labelsOk(rec),
    demoRan: demo.ok && labelsOk(demo),
    recordedTraceAddsUp: trace.ok && trace.value.view.addsUp && trace.value.view.tables.length === 2,
    recordedChangeBlocks: change.ok && change.value.verdict.block && change.value.verdict.signOff.includes("team/finance"),
    readinessListed: ready.length >= 7 && ready.every((r) => r.status === "ok" || !!r.fix),
  };
  return {
    ok: Object.values(checks).every(Boolean),
    summary: {
      checks,
      demo: demo.ok ? { origin: demo.value.view.origin, seeded: demo.value.view.seeded.map((s) => s.status).join(" "), walkthrough: demo.value.view.summary, why: demo.value.why?.split("\n")[0] ?? null } : demo.error,
      trace: trace.ok ? { headline: trace.value.view.headline, checks: trace.value.view.checks.map((x) => `${x.label} ${x.ok ? "OK" : "DOES NOT ADD UP"}`) } : trace.error,
      change: change.ok ? { severity: change.value.verdict.severity, risk: change.value.verdict.risk, signOff: change.value.verdict.signOff, notify: change.value.verdict.notify } : change.error,
      readiness: ready.map((r) => `${r.status.padEnd(8)} ${r.name}: ${r.found}`),
    },
  };
}
