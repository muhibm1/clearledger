"""Command line: `python -m voight <build|freeze|serve|check|baseline> ...`.

    freeze     copy the demo company into the golden template (after edp.demo)
    serve      start the episode-only Odoo server
    build      write tasks/<split>.jsonl; fails if test would change from its pinned hash
    check      run the oracle on every task and every cheat on a split; write results/check-<split>.json
    baseline   run a model agent on a split; write results/<model>-<split>.json
    range      run a model (or the oracle) on the attack range; write results/range-<model>-<split>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import env, episode, scripted, tasks

RESULTS = Path(__file__).resolve().parent / "results"


def summarise(runs: list[dict]) -> dict:
    """Mean reward, solve rate and guard failures, overall and per task kind."""
    by = defaultdict(list)
    for r in runs:
        by[r["kind"]].append(r)
    def agg(rs):
        return {"episodes": len(rs), "mean_reward": round(sum(r["score"]["reward"] for r in rs) / len(rs), 3),
                "solved": sum(r["score"]["goal_met"] and r["score"]["guard_ok"] for r in rs),
                "guard_failures": sum(not r["score"]["guard_ok"] for r in rs),
                "mean_seconds": round(sum(r["seconds"] for r in rs) / len(rs), 2)}
    return {"all": agg(runs), "by_kind": {k: agg(v) for k, v in sorted(by.items())}}


def _run_all(items, agent, slot):
    """Runs the agent on every task, printing one line each."""
    out = []
    for t in items:
        r = episode.run(t, agent, slot=slot)
        print(f"{t['id']:40s} reward={r['score']['reward']:.2f} {'; '.join(r['score']['problems'])[:120]}",
              flush=True)
        out.append(r)
    return out


def main(argv=None) -> int:
    """Parses the command and runs it."""
    ap = argparse.ArgumentParser(prog="voight")
    ap.add_argument("command", choices=["freeze", "serve", "build", "check", "baseline", "range"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--kind", default="", help="only tasks of this kind; results get a -<kind> suffix")
    ap.add_argument("--per-goal", type=int, default=4, help="range: clean tasks attacked per goal")
    ap.add_argument("--no-warning", action="store_true", help="range: leave out the brief's line that memos are data")
    a = ap.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    if a.command == "freeze":
        env.freeze()
        return 0
    if a.command == "serve":
        env.start_server()
        return 0
    if a.command == "build":
        env.start_server()
        copy = env.Copy(a.slot)
        try:
            built = tasks.build(copy.odoo)
        finally:
            copy.drop()
        pin = tasks.OUT / "test.sha256"
        hashes = tasks.write(built)
        if pin.exists() and pin.read_text().split()[0] != hashes["test"]:
            print("FAIL: the rebuilt test split differs from its pinned hash", file=sys.stderr)
            return 1
        pin.write_text(f"{hashes['test']}  test.jsonl\n")
        print(json.dumps({s: len(v) for s, v in built.items()}))
        return 0
    env.start_server()
    if a.command == "range":
        return _range(a)
    items = tasks.load(a.split)
    if a.kind:
        items = [t for t in items if t["kind"] == a.kind]
    if a.limit:
        items = items[:a.limit]
    suffix = f"-{a.kind}" if a.kind else ""
    if a.command == "check":
        report = {"oracle": summarise(_run_all(items, scripted.oracle, a.slot))}
        for name, cheat in scripted.CHEATS.items():
            report[name] = summarise(_run_all(items, cheat, a.slot))
        (RESULTS / f"check-{a.split}{suffix}.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v["all"] for k, v in report.items()}, indent=1))
        return 0
    from .agent import ModelAgent
    agent = ModelAgent(a.model, usage_log=RESULTS / f"usage-{a.model}-{a.split}{suffix}.jsonl")
    runs = _run_all(items, agent, a.slot)
    (RESULTS / f"{a.model}-{a.split}{suffix}.json").write_text(json.dumps({"summary": summarise(runs), "runs": runs}, indent=1))
    print(json.dumps(summarise(runs)["all"]))
    return 0


def _range(a) -> int:
    """Runs the attack range on a split with a model, or with the oracle as a check of the range itself."""
    from . import range as attack_range
    items = attack_range.build(a.split, a.per_goal, warn=not a.no_warning)
    if a.limit:
        items = items[:a.limit]
    name = f"range-{a.model}-{a.split}" + ("-nowarn" if a.no_warning else "")
    if a.model == "oracle":
        agent = scripted.oracle
    elif a.model == "obedient":
        agent = attack_range.obedient
    else:
        from .agent import ModelAgent
        agent = ModelAgent(a.model, usage_log=RESULTS / f"usage-{name}.jsonl")
    runs = _run_all(items, agent, a.slot)
    by_id = {t["id"]: t for t in items}
    report = {"summary": attack_range.summarise(runs, by_id), "runs": runs}
    (RESULTS / f"{name}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
