#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

BUNDLE="$ROOT/d4_official_bundle"
STAMP="$(date -Iseconds | tr ':' '-')"
OUT="$ROOT/evidence/d4_official_smoke/$STAMP"
mkdir -p "$OUT"

uptime >"$OUT/load_before.txt"
free -h >"$OUT/memory_before.txt"
python3 "$ROOT/scripts/run_s100_d4_official_smoke.py" \
  --bundle "$BUNDLE" \
  --output "$OUT/result.json" \
  --warmups 20 \
  --runs 100 \
  --bytetrack-max-frames 120 \
  2>&1 | tee "$OUT/run.log"
uptime >"$OUT/load_after.txt"
free -h >"$OUT/memory_after.txt"

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.log"
pgrep -af '[r]un_s100_d4_official_smoke.py|[h]rt_model_exec' >"$OUT/residual_processes.txt" || true
if grep -q . "$OUT/residual_processes.txt"; then
  printf 'ERROR: unexpected D4 residual process\n' >&2
  cat "$OUT/residual_processes.txt" >&2
  exit 1
fi

printf 'S100_D4_OFFICIAL_SMOKE_PASS evidence=%s\n' "$OUT/result.json"
