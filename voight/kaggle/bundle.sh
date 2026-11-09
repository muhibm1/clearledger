#!/usr/bin/env bash
# Packs what a Kaggle GPU run needs into one folder, uploaded as a private Kaggle dataset:
# the frozen company (a plain SQL dump and its filestore), the code, and the warm-start
# adapters and traces from earlier runs. Run from the repo root with Postgres up.
#   bash voight/kaggle/bundle.sh <out dir> [adapter dir ...]
set -euo pipefail
OUT="$1"; shift
ADAPTERS=("${@:-runs/sft-1.5b-range}")
mkdir -p "$OUT"

pg_dump --no-owner -d vtgolden | gzip -9 > "$OUT/vtgolden.sql.gz"
tar czf "$OUT/vtgolden-filestore.tgz" -C .odoo/data/filestore vtgolden

# Tracked code only, plus the adapters and oracle traces, which are git-ignored.
{ git ls-files voight clearledger analyst; ls runs/*.jsonl; find "${ADAPTERS[@]}" -type f; } \
  | tar czf "$OUT/repo.tgz" -T -

cat > "$OUT/dataset-metadata.json" <<META
{"title": "voight-bundle", "id": "${KAGGLE_USERNAME:?set KAGGLE_USERNAME}/voight-bundle",
 "licenses": [{"name": "other"}]}
META
du -sh "$OUT"/*
