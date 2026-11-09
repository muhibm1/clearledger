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

`run.py` is the job. Each run is a named entry in its `JOBS` list and `RUN` picks the one a push
executes, so the jobs that produced the numbers in the main README stay readable: `w4` (a supervised
pass on ties and overpayments, then GRPO, which Kaggle's 12 hour cap stopped at iteration 42 of 60),
`w4b` and `w4c` (supervised passes on wider trace mixes). The repo and Odoo live in `/tmp`; adapters
are written to `out/` and score files copied there after every step, so a run stopped by the cap
keeps what it finished.

Trace mixes beyond the bundle's own `runs/*.jsonl` are uploaded as separate dataset files and named
with a leading `@` in a job (`@sft-full-mix.jsonl`). `sft-full-mix.jsonl` is `sft-escalate-mix.jsonl`
plus `sft-range-mix.jsonl` plus the oracle's train steps (`oracle-traces-train.jsonl`) for
`two_credits`, `memo_short`, `partial_span` and `injection` once, `two_credits_esc` and `mixed_ref`
twice and `wrong_ref` three times, shuffled with seed 7 (1,511 rows). `sft-balance-mix.jsonl` (w4b) is
`sft-escalate-mix.jsonl` plus the oracle steps for `injection` and `two_credits_esc` twice and
`mixed_ref` and `wrong_ref` three times (766 rows).

Kaggle notes: the account needs a verified phone number for a kernel to reach the internet (without
it every download fails to resolve); Kaggle unpacks a `.gz` file on upload, which `run.py` handles;
Kaggle's own Python has no `ensurepip`, so `setup.sh` gives Odoo Python 3.11.
