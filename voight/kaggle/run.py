"""The Kaggle kernel: builds the episode environment, trains, scores, and leaves results in /kaggle/working.

Reads the bundle from the attached `voight-bundle` dataset (see bundle.sh). The job
is one entry of JOBS below, picked by RUN: training and scoring steps run in order,
each with its log in out/. Adapters are written to out/ and score files copied there
after every step, so a run that hits Kaggle's 12 hour cap keeps what it finished.
"""

import glob
import gzip
import os
import shutil
import subprocess
import sys

import torch

WORK = "/kaggle/working"
# The repo and Odoo live outside /kaggle/working, so the run's output is only out/.
REPO = "/tmp/voight-repo"
OUT = f"{WORK}/out"
START = os.environ.get("VOIGHT_START", "runs/sft-1.5b-range")
MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
# bfloat16 only where the card has it (A100, L4); a T4 or P100 trains in float32, which fits 16 GB for LoRA.
BF16 = ["--bf16"] if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else []


def score(adapter: str, name: str) -> list[list[str]]:
    """The two standard scores for one adapter: the 42-task dev subset and the attack range."""
    base = ["voight.train.evaluate", "--model", MODEL, "--adapter", adapter, "--name", name, "--split", "dev",
            "--slots", "8"]
    return [[*base, "--per-kind", "3"], [*base, "--range"]]


# Each run is one named job; RUN picks the one this push executes. Adapters are written
# straight to out/, so a run stopped by Kaggle's 12 hour cap keeps its last save.
JOBS = {
    # 2026-11-09, first GPU run: a supervised pass on ties and overpayments, then 60 GRPO
    # iterations (the cap stopped it after 42; the save at 40 is in its output).
    "w4": [
        ["voight.train.sft", "fit", "--model", MODEL, "--adapter", START, "--traces", "runs/sft-escalate-mix.jsonl",
         "--out", f"{OUT}/sft-1.5b-esc", "--lr", "1e-4", *BF16],
        *score(f"{OUT}/sft-1.5b-esc", "qwen1.5b-sft-esc"),
        ["voight.train.grpo", "--model", MODEL, "--adapter", START, "--out", f"{OUT}/grpo-gpu",
         "--iterations", "60", "--tasks-per-iter", "8", "--group", "8", "--lr", "1e-5",
         "--slots", "8", "--save-every", "10", "--attacks", "plain,authority,policy_update", *BF16],
        *score(f"{OUT}/grpo-gpu", "qwen1.5b-grpo-gpu"),
    ],
    # The w4 pass fixed ties and overpayments but lost injection, two_credits_esc and the
    # reference escalations. The same pass with those kinds' oracle steps added back
    # (sft-balance-mix.jsonl, a dataset file), scored on the subset, the range and all of dev;
    # then the w4 GRPO save at iteration 40, read from that run's output (that step found no
    # save there: the w4 output had no runs/grpo-gpu, so it stopped).
    "w4b": [
        ["voight.train.sft", "fit", "--model", MODEL, "--adapter", START, "--traces", "@sft-balance-mix.jsonl",
         "--out", f"{OUT}/sft-1.5b-bal", "--lr", "1e-4", *BF16],
        *score(f"{OUT}/sft-1.5b-bal", "qwen1.5b-sft-bal"),
        ["voight.train.evaluate", "--model", MODEL, "--adapter", f"{OUT}/sft-1.5b-bal", "--name", "qwen1.5b-sft-bal",
         "--split", "dev", "--slots", "8"],
        *score("@grpo-gpu", "qwen1.5b-grpo-gpu-it40"),
    ],
    # w4b reached 79/97 on dev but lost two_credits (0/9) and let 12 of 18 redirect attacks
    # through. Every source at once: the w4 pass, the attack-range pass the warm start came
    # from, and oracle steps for the kinds either one under-covers (sft-full-mix.jsonl).
    "w4c": [
        ["voight.train.sft", "fit", "--model", MODEL, "--adapter", START, "--traces", "@sft-full-mix.jsonl",
         "--out", f"{OUT}/sft-1.5b-full", "--lr", "1e-4", *BF16],
        *score(f"{OUT}/sft-1.5b-full", "qwen1.5b-sft-full"),
        ["voight.train.evaluate", "--model", MODEL, "--adapter", f"{OUT}/sft-1.5b-full", "--name", "qwen1.5b-sft-full",
         "--split", "dev", "--slots", "8"],
    ],
    # The one scoring of the held-out test split, for the frozen w4c adapter read from that
    # run's output: all of test, then the attack range built from test customers.
    "w4c-test": [
        ["voight.train.evaluate", "--model", MODEL, "--adapter", "@sft-1.5b-full", "--name", "qwen1.5b-sft-full",
         "--split", "test", "--slots", "8"],
        ["voight.train.evaluate", "--model", MODEL, "--adapter", "@sft-1.5b-full", "--name", "qwen1.5b-sft-full",
         "--split", "test", "--range", "--slots", "8"],
    ],
    # 2026-11-16, one bounded GRPO try from the frozen w4c adapter (sft-1.5b-full, in the bundle at
    # runs/sft-1.5b-full): 14 iterations of 8 tasks x 8 attempts, drawn from the weak train kinds plus
    # delimiter-attacked train tasks, capped at 4.5 hours by "timeout:". Then each score is taken once:
    # the oracle on the pinned attack round 2 (a check of the round itself), the GRPO adapter on all of
    # test and on round 2, and the starting adapter on round 2 for comparison. No dev scoring, no picking.
    "r2": [
        ["voight.attacks_r2", "verify"],
        ["voight.attacks_r2", "check"],
        ["timeout:16200", "voight.train.grpo", "--model", MODEL, "--adapter", "runs/sft-1.5b-full",
         "--out", f"{OUT}/grpo-r2", "--iterations", "14", "--tasks-per-iter", "8", "--group", "8", "--lr", "2e-5",
         "--kl", "0.01", "--slots", "8", "--save-every", "2", "--kinds", "two_credits,typo_ref,mixed_ref,tie",
         "--attacks", "delimiter", *BF16],
        ["voight.train.evaluate", "--model", MODEL, "--adapter", f"{OUT}/grpo-r2", "--name", "qwen1.5b-grpo-r2",
         "--split", "test", "--slots", "8"],
        ["voight.train.evaluate", "--model", MODEL, "--adapter", f"{OUT}/grpo-r2", "--name", "qwen1.5b-grpo-r2",
         "--attacks-r2", "--slots", "8"],
        ["voight.train.evaluate", "--model", MODEL, "--adapter", "runs/sft-1.5b-full", "--name", "qwen1.5b-sft-full",
         "--attacks-r2", "--slots", "8"],
    ],
    # 2026-11-17, an independent repeat of r2 to check its test number: the same recipe and starting
    # adapter, a different seed (1, so different batches), stopped at exactly 10 iterations, then the
    # test split scored once. Nothing else is run.
    "r2-repeat": [
        ["timeout:16200", "voight.train.grpo", "--model", MODEL, "--adapter", "runs/sft-1.5b-full",
         "--out", f"{OUT}/grpo-r2-repeat", "--iterations", "10", "--tasks-per-iter", "8", "--group", "8",
         "--lr", "2e-5", "--kl", "0.01", "--slots", "8", "--save-every", "2", "--seed", "1",
         "--kinds", "two_credits,typo_ref,mixed_ref,tie", "--attacks", "delimiter", *BF16],
        ["voight.train.evaluate", "--model", MODEL, "--adapter", f"{OUT}/grpo-r2-repeat",
         "--name", "qwen1.5b-grpo-r2-repeat", "--split", "test", "--slots", "8"],
    ],
}
RUN = os.environ.get("VOIGHT_RUN", "r2")


def find(name: str) -> str:
    """The bundle file with this name, wherever Kaggle mounted the dataset."""
    hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
    if not hits:
        sys.exit(f"bundle file missing: {name}")
    return hits[0]


def bundle_dir() -> str:
    """The folder setup.sh reads, with the dump gzipped as it expects.

    Kaggle unpacks a .gz file on upload, so the dataset can hold vtgolden.sql rather
    than vtgolden.sql.gz. In that case the dump is gzipped again into a staging folder
    next to a copy of the filestore archive.
    """
    gz = glob.glob("/kaggle/input/**/vtgolden.sql.gz", recursive=True)
    if gz:
        return os.path.dirname(gz[0])
    stage = "/tmp/voight-bundle"
    os.makedirs(stage, exist_ok=True)
    with open(find("vtgolden.sql"), "rb") as src, gzip.open(f"{stage}/vtgolden.sql.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    shutil.copy(find("vtgolden-filestore.tgz"), stage)
    return stage


def save() -> None:
    """Copies the score files so far to out/ (adapters are written there directly)."""
    if os.path.isdir(f"{REPO}/voight/results"):
        shutil.copytree(f"{REPO}/voight/results", f"{OUT}/results", dirs_exist_ok=True)


def resolve(arg: str) -> str:
    """An argument as given, or for "@name" the path of that file or adapter in an attached input."""
    if not arg.startswith("@"):
        return arg
    name = arg[1:]
    hits = glob.glob(f"/kaggle/input/**/{name}/adapter_config.json", recursive=True)
    return os.path.dirname(hits[0]) if hits else find(name)


def sh(args: list[str], log: str, ok: tuple = (), **kw) -> None:
    """Runs one step with its output in a log file under out/, and stops the kernel if it fails."""
    print("$", " ".join(args), flush=True)
    with open(f"{OUT}/{log}", "a") as f:
        code = subprocess.call(args, stdout=f, stderr=subprocess.STDOUT, **kw)
    if code and code not in ok:
        save()
        sys.exit(f"step failed ({code}); see out/{log}")


def main() -> None:
    """Setup, then each job step in order, then copy results and adapters to out/."""
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(REPO, exist_ok=True)
    sh(["tar", "xzf", find("repo.tgz"), "-C", REPO], "setup.log")
    bundle = bundle_dir()
    sh(["bash", "voight/kaggle/setup.sh", bundle], "setup.log", cwd=REPO)
    env = {**os.environ, "PGHOST": "localhost", "PGUSER": "odoo", "PGPASSWORD": "odoo-local",
           "LANGSMITH_TRACING": "false", "PYTHONPATH": REPO}
    for i, step in enumerate(JOBS[RUN]):
        wrap = []
        if step[0].startswith("timeout:"):  # a step that must stop by itself; the adapter keeps its last save
            wrap, step = ["timeout", step[0].split(":")[1]], step[1:]
        sh([*wrap, sys.executable, "-m", *map(resolve, step)], f"step{i}.log", cwd=REPO, env=env,
                  ok=(124,) if wrap else ())
        save()
    print("done", flush=True)


if __name__ == "__main__":
    main()
