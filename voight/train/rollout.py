"""Runs many episodes at once against one batched model, one database slot each."""

from __future__ import annotations

import threading
import traceback

from .. import episode

FIRST_SLOT = 10  # slots 0-9 are left to scripted checks and API baselines


def run_many(items: list[dict], batcher, slots: int, first_slot: int = FIRST_SLOT, max_steps: int = 8) -> list[dict]:
    """Runs every (task, key) in `items` with at most `slots` episodes live; returns results in order.

    Each item is {"task": spec, "key": unique episode key}. A result carries the
    episode's score and transcript plus `tokens`, the (prompt, completion) id
    pairs the batcher recorded for it.
    """
    results: list[dict | None] = [None] * len(items)
    free = list(range(first_slot, first_slot + slots))
    gate = threading.Condition()

    def worker(i: int, item: dict):
        """Runs one episode on a free slot and stores its result."""
        with gate:
            gate.wait_for(lambda: free)
            slot = free.pop()
        done = batcher.episode(item["key"])
        try:
            r = episode.run(item["task"], batcher, slot=slot, max_steps=max_steps)
        except Exception as exc:  # an environment failure is recorded, never silently scored
            r = {"task": item["task"]["id"], "kind": item["task"]["kind"], "error": repr(exc),
                 "trace": traceback.format_exc()[-800:], "score": None, "steps": 0}
        finally:
            done()
            with gate:
                free.append(slot)
                gate.notify_all()
        r["key"] = item["key"]
        r["tokens"] = batcher.records.pop(item["key"], [])
        results[i] = r

    threads = [threading.Thread(target=worker, args=(i, it)) for i, it in enumerate(items)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results
