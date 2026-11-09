# Training Voight on a Kaggle GPU

The CPU runs in `voight/train/` take hours per pass. This folder moves one longer run onto a free
Kaggle GPU. Everything the run needs travels as a private Kaggle dataset; the kernel builds the
same episode environment there (Postgres 16, Odoo 17, the frozen company) and trains against it.

1. Build the bundle, from the repo root with Postgres up:
   `KAGGLE_USERNAME=<you> bash voight/kaggle/bundle.sh /tmp/voight-bundle`
   It holds a plain SQL dump of `vtgolden`, its filestore, the tracked code, the oracle traces and
   the warm start adapter (`runs/sft-1.5b-range` unless others are named after the folder).
2. Upload it: `kaggle datasets create -p /tmp/voight-bundle` (later: `kaggle datasets version`).
3. Put your user name into `kernel-metadata.json` in place of `KAGGLE_USERNAME`, then
   `kaggle kernels push -p voight/kaggle`. The kernel runs on a GPU with internet on, because it
   installs Postgres and Odoo and downloads the base model.
4. When it finishes: `kaggle kernels output <you>/voight-grpo -p runs/kaggle`. `out/results` holds
   the score files and `out/grpo-gpu` the trained adapter.

`run.py` is the job: GRPO for 60 iterations of 8 tasks with 8 attempts each, half of every batch
attacked with the three trained techniques, then the dev subset and the attack range. It trains in
bfloat16 where the card supports it and in float32 otherwise (T4, P100).
