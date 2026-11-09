"""The Kaggle kernel: builds the episode environment, trains, scores, and leaves results in /kaggle/working.

Reads the bundle from the attached `voight-bundle` dataset (see bundle.sh). The job
is the JOB list below: a supervised pass on ties and overpayments from the warm start,
then one longer GRPO run from the warm start, mixing attacked tasks into training, each
scored on the dev subset and the attack range. Results and adapters are copied to
/kaggle/working/out after every step, which Kaggle keeps when the run ends.
"""

import glob
import gzip
import os
import shutil
import subprocess
import sys

import torch

WORK = "/kaggle/working"
REPO = f"{WORK}/repo"
OUT = f"{WORK}/out"
START = os.environ.get("VOIGHT_START", "runs/sft-1.5b-range")
MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
# bfloat16 only where the card has it (A100, L4); a T4 or P100 trains in float32, which fits 16 GB for LoRA.
ADAPTERS = ["sft-1.5b-esc", "grpo-gpu"]
BF16 = ["--bf16"] if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else []

JOB = [
    # A supervised pass on ties and overpayments, which the warm start still misses (0/3 each on the dev subset).
    ["voight.train.sft", "fit", "--model", MODEL, "--adapter", START, "--traces", "runs/sft-escalate-mix.jsonl",
     "--out", "runs/sft-1.5b-esc", "--lr", "1e-4", *BF16],
    ["voight.train.evaluate", "--model", MODEL, "--adapter", "runs/sft-1.5b-esc", "--name", "qwen1.5b-sft-esc",
     "--split", "dev", "--per-kind", "3", "--slots", "8"],
    ["voight.train.evaluate", "--model", MODEL, "--adapter", "runs/sft-1.5b-esc", "--name", "qwen1.5b-sft-esc",
     "--split", "dev", "--range", "--slots", "8"],
    ["voight.train.grpo", "--model", MODEL, "--adapter", START, "--out", "runs/grpo-gpu",
     "--iterations", "60", "--tasks-per-iter", "8", "--group", "8", "--lr", "1e-5",
     "--slots", "8", "--save-every", "10", "--attacks", "plain,authority,policy_update", *BF16],
    ["voight.train.evaluate", "--model", MODEL, "--adapter", "runs/grpo-gpu", "--name", "qwen1.5b-grpo-gpu",
     "--split", "dev", "--per-kind", "3", "--slots", "8"],
    ["voight.train.evaluate", "--model", MODEL, "--adapter", "runs/grpo-gpu", "--name", "qwen1.5b-grpo-gpu",
     "--split", "dev", "--range", "--slots", "8"],
]


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
    """Copies whatever results and adapters exist so far to out/."""
    if os.path.isdir(f"{REPO}/voight/results"):
        shutil.copytree(f"{REPO}/voight/results", f"{OUT}/results", dirs_exist_ok=True)
    for name in ADAPTERS:
        if os.path.isdir(f"{REPO}/runs/{name}"):
            shutil.copytree(f"{REPO}/runs/{name}", f"{OUT}/{name}", dirs_exist_ok=True)


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
    for i, step in enumerate(JOB):
        sh([sys.executable, "-m", *step], f"step{i}.log", cwd=REPO, env=env)
        save()
    print("done", flush=True)


if __name__ == "__main__":
    main()
