"""GRPO in Voight: try each task several times, reward what the checker rewards, repeat.

GRPO (group relative policy optimisation) needs no second "critic" model. For
each task the model makes G attempts. Each attempt's advantage is its reward
minus the group's mean, divided by the group's spread: better-than-its-siblings
attempts are pushed up, worse ones down. A group where every attempt scored the
same teaches nothing and is skipped.

One episode is several model turns (look up invoices, look up credits, apply,
finish). The episode's single advantage is applied to every turn's tokens.
Each batch is used for one update, so the policy ratio is 1 and the clipped
objective reduces to a policy gradient with the group baseline. An optional KL
term keeps the model near where it started; the reference is the same weights
with the LoRA adapter switched off, so it costs no extra memory.

    python -m voight.train.grpo --model Qwen/Qwen2.5-0.5B-Instruct --adapter runs/sft-0.5b \
        --out runs/grpo-0.5b --iterations 20 --tasks-per-iter 4 --group 4

Every iteration's rewards, skipped groups and loss go to <out>/log.jsonl, and
the adapter is saved every few iterations, so a long CPU run can be stopped and
resumed from its last save.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path

import torch

from .. import env, tasks
from . import lm, rollout


def _logprobs(model, prompt: list[int], completion: list[int]) -> torch.Tensor:
    """Log-probability of each completion token given the prompt and the tokens before it."""
    ids = torch.tensor([prompt + completion])
    logits = model(input_ids=ids).logits[0, len(prompt) - 1:-1]
    return torch.log_softmax(logits.float(), dim=-1).gather(1, torch.tensor(completion)[:, None])[:, 0]


def advantages(rewards: list[float]) -> list[float] | None:
    """Group-normalised advantages, or None when every attempt scored the same."""
    if len(set(rewards)) == 1:
        return None
    mean, sd = statistics.fmean(rewards), statistics.pstdev(rewards)
    return [(r - mean) / (sd + 1e-4) for r in rewards]


def main(argv=None) -> int:
    """Runs the training loop."""
    from peft import LoraConfig, PeftModel, get_peft_model
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--adapter", default=None, help="start from this LoRA adapter (e.g. the SFT warm start)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--tasks-per-iter", type=int, default=4)
    ap.add_argument("--group", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--kl", type=float, default=0.0)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--slots", type=int, default=8)
    ap.add_argument("--save-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args(argv)
    if a.threads:
        torch.set_num_threads(a.threads)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env.start_server()
    base, tok = lm.load(a.model)
    if a.adapter:
        model = PeftModel.from_pretrained(base, a.adapter, is_trainable=True)
    else:
        model = get_peft_model(base, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0, task_type="CAUSAL_LM",
                                                target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                                                "gate_proj", "up_proj", "down_proj"]))
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr)
    train = tasks.load("train")
    rng = random.Random(a.seed)
    log_path = out / "log.jsonl"
    started = time.time()
    for it in range(a.iterations):
        t0 = time.time()
        picked = rng.sample(train, a.tasks_per_iter)
        model.eval()
        batcher = lm.Batcher(model, tok, temperature=a.temperature)
        items = [{"task": t, "key": f"{it}:{t['id']}:{g}"} for t in picked for g in range(a.group)]
        runs = rollout.run_many(items, batcher, slots=a.slots)
        batcher.close()
        rollout_s = time.time() - t0
        model.train()
        losses, used, skipped, errors = [], 0, 0, 0
        for t in picked:
            group = [r for r in runs if r["key"].split(":")[1] == t["id"]]
            if any(r.get("score") is None for r in group):
                errors += 1
                continue
            adv = advantages([r["score"]["reward"] for r in group])
            if adv is None:
                skipped += 1
                continue
            used += 1
            for r, A in zip(group, adv):
                for prompt, completion in r["tokens"]:
                    if not completion:
                        continue
                    lp = _logprobs(model, prompt, completion)
                    loss = -(A * lp.mean())
                    if a.kl:
                        with torch.no_grad(), model.disable_adapter():
                            ref = _logprobs(model, prompt, completion)
                        d = ref - lp
                        loss = loss + a.kl * (torch.exp(d) - d - 1).mean()
                    (loss / (a.tasks_per_iter * a.group)).backward()
                    losses.append(loss.item())
        if used:
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            opt.step()
        opt.zero_grad()
        rewards = [r["score"]["reward"] for r in runs if r.get("score")]
        row = {"iteration": it, "tasks": [t["id"] for t in picked],
               "mean_reward": round(statistics.fmean(rewards), 3) if rewards else None,
               "solved": sum(r["score"]["goal_met"] and r["score"]["guard_ok"] for r in runs if r.get("score")),
               "episodes": len(runs), "groups_used": used, "groups_skipped": skipped, "errors": errors,
               "unparsed": batcher.stats["unparsed"],
               "mean_loss": round(statistics.fmean(losses), 4) if losses else None,
               "rollout_seconds": round(rollout_s, 1), "update_seconds": round(time.time() - t0 - rollout_s, 1),
               "elapsed": round(time.time() - started, 1)}
        with log_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
        if (it + 1) % a.save_every == 0 or it + 1 == a.iterations:
            model.save_pretrained(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
