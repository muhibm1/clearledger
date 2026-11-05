"""Warm start: teach a small model the tool-call format from the oracle's own episodes.

A model of 0.5B to 1.5B parameters rarely gets any reward at first: it writes
prose, or calls tools with the wrong arguments. GRPO learns nothing from a
group where every attempt scores zero, so it needs a model that already
succeeds sometimes. The usual answer is a short supervised pass first.

    python -m voight.train.sft traces          # run the oracle on train, keep every step
    python -m voight.train.sft fit --model Qwen/Qwen2.5-0.5B-Instruct --out runs/sft-0.5b

Only train tasks are used. The oracle reads tool results, never the answer key,
so the traces show the policy being followed through the tools.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import torch

from .. import episode, scripted, tasks
from . import lm

RUNS = Path(__file__).resolve().parent.parent.parent / "runs"
TRACES = RUNS / "oracle-traces-train.jsonl"


def traces(split: str = "train", slot: int = 9) -> int:
    """Runs the oracle on every task of the split and writes each solved step as a training example."""
    RUNS.mkdir(exist_ok=True)
    n = 0
    with TRACES.open("w") as f:
        for t in tasks.load(split):
            steps = []

            def recorder(brief, history, _steps=steps):
                """Calls the oracle and keeps what it saw and what it answered."""
                call = scripted.oracle(brief, history)
                _steps.append({"messages": lm.messages(brief, list(history)),
                               "target": {"tool": call["tool"], "args": call.get("args") or {}}})
                return call

            r = episode.run(t, recorder, slot=slot)
            if r["score"]["reward"] != 1.0:
                print("oracle did not solve", t["id"], r["score"]["problems"])
                continue
            for s in steps:
                f.write(json.dumps({"task": t["id"], "kind": t["kind"], **s}) + "\n")
                n += 1
    print(f"{n} examples in {TRACES}")
    return n


def _example(tok, row: dict) -> tuple[list[int], list[int]]:
    """Prompt ids and target ids (the JSON call and end-of-turn) for one step."""
    prompt = tok.apply_chat_template(row["messages"], tokenize=False, add_generation_prompt=True)
    target = json.dumps(row["target"], separators=(",", ":"))
    p = tok(prompt, add_special_tokens=False)["input_ids"]
    end = tok.convert_tokens_to_ids("<|im_end|>")
    t = tok(target, add_special_tokens=False)["input_ids"] + [end if end is not None else tok.eos_token_id]
    return p, t


def fit(model_name: str, out: str, epochs: int = 1, lr: float = 2e-4, rank: int = 16, limit: int = 0,
        seed: int = 0, threads: int = 0) -> dict:
    """LoRA fine-tune on the oracle traces: loss on the target tokens only."""
    from peft import LoraConfig, get_peft_model
    if threads:
        torch.set_num_threads(threads)
    rows = [json.loads(line) for line in TRACES.read_text().splitlines()]
    random.Random(seed).shuffle(rows)
    rows = rows[: limit or None]
    model, tok = lm.load(model_name)
    model = get_peft_model(model, LoraConfig(r=rank, lora_alpha=2 * rank, lora_dropout=0.0,
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                                             "gate_proj", "up_proj", "down_proj"],
                                             task_type="CAUSAL_LM"))
    model.train()
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)
    log = []
    started = time.time()
    for epoch in range(epochs):
        for i, row in enumerate(rows):
            p, t = _example(tok, row)
            ids = torch.tensor([p + t])
            labels = torch.tensor([[-100] * len(p) + t])
            loss = model(input_ids=ids, labels=labels).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
            log.append({"epoch": epoch, "step": i, "loss": round(loss.item(), 4),
                        "seconds": round(time.time() - started, 1)})
            if i % 20 == 0:
                print(json.dumps(log[-1]), flush=True)
    Path(out).mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    summary = {"model": model_name, "examples": len(rows), "epochs": epochs, "lr": lr, "rank": rank,
               "first_loss": log[0]["loss"], "last_20_mean_loss": round(sum(x["loss"] for x in log[-20:]) / min(20, len(log)), 4),
               "seconds": round(time.time() - started, 1)}
    (Path(out) / "train-log.json").write_text(json.dumps({"summary": summary, "log": log}, indent=1))
    print(json.dumps(summary))
    return summary


def main(argv=None) -> int:
    """`traces` or `fit`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["traces", "fit"])
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--out", default=str(RUNS / "sft-0.5b"))
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args(argv)
    if a.command == "traces":
        traces()
    else:
        fit(a.model, a.out, a.epochs, a.lr, limit=a.limit, threads=a.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
