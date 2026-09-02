#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

BUNDLE="$ROOT/bpu_bundle"
STAMP="$(date -Iseconds | tr ':' '-')"
OUT="$ROOT/evidence/d3_bpu_ab/$STAMP"
mkdir -p "$OUT"

test -f "$BUNDLE/manifest.json"
test -f "$ROOT/scripts/run_s100_bpu_ab.py"

python3 "$ROOT/scripts/run_s100_bpu_ab.py" \
  --bundle "$BUNDLE" \
  --output "$OUT/result.json" \
  --warmups 20 \
  --runs 200 \
  2>&1 | tee "$OUT/run.log"

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.log"
pgrep -af '[r]un_s100_bpu_ab.py|[h]rt_model_exec' >"$OUT/residual_processes.txt" || true
if grep -q . "$OUT/residual_processes.txt"; then
  printf 'ERROR: unexpected D3 residual process\n' >&2
  cat "$OUT/residual_processes.txt" >&2
  exit 1
fi

printf 'S100_D3_BPU_AB_PASS evidence=%s\n' "$OUT/result.json"
