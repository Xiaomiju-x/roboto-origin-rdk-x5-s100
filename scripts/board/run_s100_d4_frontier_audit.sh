#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

OFFICIAL_RESULT="$ROOT/evidence/d4_official_smoke/2026-08-29T18-51-14+08-00/result.json"
STAMP="$(date -Iseconds | tr ':' '-')"
OUT="$ROOT/evidence/d4_frontier_audit/$STAMP"
mkdir -p "$OUT"

python3 "$ROOT/scripts/run_s100_d4_frontier_audit.py" \
  --decision "$ROOT/config/s100_d4_frontier_decision.json" \
  --official-result "$OFFICIAL_RESULT" \
  --output "$OUT/result.json" \
  2>&1 | tee "$OUT/run.log"

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.log"
pgrep -af '[r]un_s100_d4_frontier_audit.py|[h]rt_model_exec' >"$OUT/residual_processes.txt" || true
if grep -q . "$OUT/residual_processes.txt"; then
  printf 'ERROR: unexpected D4 audit residual process\n' >&2
  cat "$OUT/residual_processes.txt" >&2
  exit 1
fi

printf 'S100_D4_FRONTIER_AUDIT_PASS evidence=%s\n' "$OUT/result.json"
