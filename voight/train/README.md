# Training a small open model in Voight

**Can a model small enough to run on one cheap machine learn this finance job as well as a large hosted model?** This folder is the training half of [Voight](../README.md). It runs a small open model (Qwen2.5, 0.5B or 1.5B parameters) as the agent, scores it with the same checker as everything else, and trains it.

Training has two steps, in the usual order for agent post-training:

1. **Warm start (SFT, supervised fine-tuning).** A scripted agent that follows the policy solves every train task through the tools. Each of its steps (what it saw, which tool it called) becomes a training example, and the small model is fine-tuned to produce the same call. This mostly teaches the tool-call format and the order of work. It only ever sees train customers.
2. **GRPO (group relative policy optimisation), a kind of reinforcement learning.** The model tries each train task several times. The checker scores every attempt. Attempts that beat their siblings are made more likely, worse ones less likely. No answer key is shown; the reward is the only signal. This is where the model has to learn the judgement calls: when to escalate, which invoice comes first, ignoring an instruction planted in a memo.

Both steps train a LoRA adapter (a thin set of extra weights, a few million numbers, on top of the frozen model), so a run saves a file of tens of megabytes, not a new model.

## Files

| File | What it does |
| --- | --- |
| [lm.py](lm.py) | The model as a Voight agent. Episodes run in threads, one database copy each; a batcher collects their requests and generates all replies in one call, keeping every step's tokens for training. |
| [rollout.py](rollout.py) | Runs many episodes at once on slots 10 and up. |
| [evaluate.py](evaluate.py) | Scores a model, with or without an adapter, on a split, greedily. Same summary shape as the hosted-model baselines. |
| [sft.py](sft.py) | `traces` records the oracle's steps on train; `fit` trains the warm-start adapter. |
| [grpo.py](grpo.py) | The GRPO loop: sample tasks, G attempts each, group-normalised advantages, one update per batch, optional KL to the starting model. |

## Running it

```
python3.11 -m venv .venv-train
.venv-train/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1
.venv-train/bin/pip install transformers==4.46.3 peft==0.13.2 accelerate==1.1.1 "psycopg[binary]==3.2.3"
.venv-train/bin/python -m voight.train.evaluate --model Qwen/Qwen2.5-0.5B-Instruct --split dev
.venv-train/bin/python -m voight.train.sft traces
.venv-train/bin/python -m voight.train.sft fit --model Qwen/Qwen2.5-0.5B-Instruct --out runs/sft-0.5b
.venv-train/bin/python -m voight.train.grpo --adapter runs/sft-0.5b --out runs/grpo-0.5b
.venv-train/bin/python -m voight.train.evaluate --adapter runs/grpo-0.5b --split dev
```

Licences: PyTorch BSD-3-Clause, Transformers and PEFT Apache-2.0, Accelerate Apache-2.0. Qwen2.5-0.5B-Instruct and 1.5B-Instruct are Apache-2.0.

## Results

All rows below are Qwen2.5-0.5B-Instruct on CPU, scored greedily on a fixed dev subset: the first 3 tasks of each of the 14 kinds, 42 tasks ([../results/](../results/)).

| Model | Solved (42) | Mean reward | Tool calls it could not parse |
| --- | --- | --- | --- |
| Untrained | 0 | 0.00 | many: it writes prose and invents arguments |
| After warm start (841 oracle steps, one pass, 84 minutes on 4 CPU cores) | 16 | 0.41 | 0 |
| After GRPO (30 iterations of 4 tasks x 4 attempts from the warm start, 3.6 hours on 4 CPU cores) | 18 | 0.46 | 0 |

The warm start teaches the format and the happy path. The model solves every `memo_one`, `credit_note`, `exact`, `partial` and `typo_ref` task in the subset, and never escalates: 18 of its 26 misses are escalations it did not make (`tie`, `over`, `wrong_ref`, `mixed_ref`, `injection`, `two_credits_esc`). That is the gap GRPO is for: those tasks give a group of attempts different rewards as soon as one attempt escalates.

GRPO moved the score a little and in one place: `memo_short` went from 1 to 3 of 3 (pay the first named invoice in full, the rest to the second). Nothing else changed, and the model still escalated zero times in 42 tasks. Training reward was noisy (each iteration samples different tasks): mean 0.24 over the first ten iterations, 0.29 over the last ten.

Why escalation did not move: GRPO can only push up behaviour the model already tries. 79 of the 841 warm-start examples (9%) are an `escalate` call, and the warm-started model never produces one, so on an escalation task every attempt in a group scores 0, the group has no spread, and it is skipped. A tie episode shows the shape: the model lists two invoices of 7,425.00, sees a payment of 7,425.00, and pays the one due later. The fixes are known and cheap to try: more sampling (higher temperature or larger groups on escalation kinds), a warm start that weights the escalate step, and above all a bigger model. A 1.5B model on a GPU is the next run; CPU is the limit here, at about 8.5 minutes per iteration for 16 episodes.

What this run does show: the whole loop works end to end on a laptop-class machine. Rollouts run in parallel database copies, the checker scores every attempt, GRPO updates a LoRA adapter, and the adapter is scored by the same code that scores the hosted models. In all three dev runs of the small model (126 episodes) nothing in the books outside the task moved: zero guard failures.
