#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

PERCEPTION="$ROOT/evidence/d4_official_smoke/2026-08-29T18-51-14+08-00/result.json"
NAVIGATION="$(find "$ROOT/evidence/d5_nav2_multigoal" -mindepth 2 -maxdepth 2 -name result.json -print | sort | tail -1)"
STAMP="$(date -Iseconds | tr ':' '-')"
OUT="$ROOT/evidence/d5_chain_stress/$STAMP"
mkdir -p "$OUT"
printf '%s\n' "$OUT" >"$ROOT/evidence/d5_chain_stress/latest_path.txt"
test -f "$PERCEPTION"
test -f "$NAVIGATION"
test -f "$ROOT/bpu_bundle/manifest.json"

uptime >"$OUT/load_before.txt"
free -h >"$OUT/memory_before.txt"
python3 "$ROOT/scripts/run_s100_d5_chain_stress.py" \
  --root "$ROOT" \
  --bundle "$ROOT/bpu_bundle" \
  --perception-result "$PERCEPTION" \
  --nav-result "$NAVIGATION" \
  --output "$OUT/result.json" \
  --policy-steps 10000 \
  --stress-seconds 1800 \
  --monitor-period 10 \
  2>&1 | tee "$OUT/run.log"
uptime >"$OUT/load_after.txt"
free -h >"$OUT/memory_after.txt"

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.log"
pgrep -af '[r]un_s100_d5_chain_stress.py|[h]rt_model_exec' >"$OUT/residual_processes.txt" || true
if grep -q . "$OUT/residual_processes.txt"; then
  printf 'ERROR: unexpected D5 residual process\n' >&2
  cat "$OUT/residual_processes.txt" >&2
  exit 1
fi

printf 'S100_D5_CHAIN_STRESS_PASS evidence=%s\n' "$OUT/result.json"
