"""Scores a local open model (optionally with a LoRA adapter) on a Voight split, greedily.

    python -m voight.train.evaluate --model Qwen/Qwen2.5-0.5B-Instruct --split dev --slots 6

Writes voight/results/local-<name>-<split>.json with the same summary shape as
the API baselines, so the numbers sit side by side.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch

from .. import env, tasks
from ..__main__ import RESULTS, summarise
from . import lm, rollout


def main(argv=None) -> int:
    """Parses arguments, runs the split, writes the results file."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--name", default=None, help="label for the results file")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--slots", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--per-kind", type=int, default=0,
                    help="only the first N tasks of each kind, for a fixed, quicker subset on CPU")
    ap.add_argument("--range", action="store_true", help="run the attack range instead of a plain split")
    ap.add_argument("--no-warning", action="store_true", help="range: leave out the brief's line that memos are data")
    a = ap.parse_args(argv)
    if a.threads:
        torch.set_num_threads(a.threads)
    env.start_server()
    model, tok = lm.load(a.model, a.adapter)
    batcher = lm.Batcher(model, tok, temperature=0)
    if a.range:
        return _range(a, batcher)
    items = tasks.load(a.split)
    if a.per_kind:
        seen: dict[str, int] = {}
        items = [t for t in items if seen.setdefault(t["kind"], 0) < a.per_kind
                 and not seen.__setitem__(t["kind"], seen[t["kind"]] + 1)]
    items = items[: a.limit or None]
    started = time.time()
    runs = rollout.run_many([{"task": t, "key": t["id"]} for t in items], batcher, slots=a.slots)
    batcher.close()
    errors = [r for r in runs if r.get("score") is None]
    scored = [r for r in runs if r.get("score") is not None]
    for r in scored:
        r.pop("tokens", None)
    name = a.name or (a.model.split("/")[-1] + ("+" + Path(a.adapter).name if a.adapter else ""))
    out = {"model": a.model, "adapter": a.adapter, "split": a.split, "summary": summarise(scored),
           "errors": errors, "batcher": batcher.stats, "wall_seconds": round(time.time() - started, 1),
           "runs": scored}
    RESULTS.mkdir(exist_ok=True)
    subset = f"-{a.per_kind}per" if a.per_kind else ""
    path = RESULTS / f"local-{name}-{a.split}{subset}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"summary": out["summary"]["all"], "errors": len(errors), "batcher": batcher.stats,
                      "wall_seconds": out["wall_seconds"], "file": str(path)}))
    return 0


def _range(a, batcher) -> int:
    """Runs the attack range with the local model and writes results/range-local-<name>-<split>.json."""
    from .. import range as attack_range
    items = attack_range.build(a.split, warn=not a.no_warning)[: a.limit or None]
    started = time.time()
    runs = rollout.run_many([{"task": t, "key": t["id"]} for t in items], batcher, slots=a.slots)
    batcher.close()
    for r in runs:
        r.pop("tokens", None)
    scored = [r for r in runs if r.get("score") is not None]
    by_id = {t["id"]: t for t in items}
    name = a.name or (a.model.split("/")[-1] + ("+" + Path(a.adapter).name if a.adapter else ""))
    out = {"model": a.model, "adapter": a.adapter, "split": a.split, "warning": not a.no_warning,
           "summary": attack_range.summarise(scored, by_id), "errors": [r for r in runs if r.get("score") is None],
           "wall_seconds": round(time.time() - started, 1), "runs": scored}
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"range-local-{name}-{a.split}{'-nowarn' if a.no_warning else ''}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"summary": out["summary"], "errors": len(out["errors"]), "file": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
