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
    # then the w4 GRPO save at iteration 40, read from that run's output.
    "w4b": [
        ["voight.train.sft", "fit", "--model", MODEL, "--adapter", START, "--traces", "@sft-balance-mix.jsonl",
         "--out", f"{OUT}/sft-1.5b-bal", "--lr", "1e-4", *BF16],
        *score(f"{OUT}/sft-1.5b-bal", "qwen1.5b-sft-bal"),
        ["voight.train.evaluate", "--model", MODEL, "--adapter", f"{OUT}/sft-1.5b-bal", "--name", "qwen1.5b-sft-bal",
         "--split", "dev", "--slots", "8"],
        *score("@grpo-gpu", "qwen1.5b-grpo-gpu-it40"),
    ],
}
RUN = "w4b"


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


def sh(args: list[str], log: str, **kw) -> None:
    """Runs one step with its output in a log file under out/, and stops the kernel if it fails."""
    print("$", " ".join(args), flush=True)
    with open(f"{OUT}/{log}", "a") as f:
        code = subprocess.call(args, stdout=f, stderr=subprocess.STDOUT, **kw)
    if code:
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
        sh([sys.executable, "-m", *map(resolve, step)], f"step{i}.log", cwd=REPO, env=env)
        save()
    print("done", flush=True)


if __name__ == "__main__":
    main()
