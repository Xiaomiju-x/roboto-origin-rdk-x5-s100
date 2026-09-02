#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

STAMP="$(date -Iseconds | tr ':' '-')"
OUT="$ROOT/evidence/d6_clean_rebuild/$STAMP"
DEST="$ROOT/rebuild/d6_$STAMP"
mkdir -p "$OUT" "$ROOT/rebuild"
test ! -e "$DEST"
printf '%s\n' "$OUT" >"$ROOT/evidence/d6_clean_rebuild/latest_path.txt"

uptime >"$OUT/load_before.txt"
free -h >"$OUT/memory_before.txt"
set +e
python3 "$ROOT/scripts/run_s100_d6_clean_rebuild.py" \
  --root "$ROOT" \
  --destination "$DEST" \
  --output "$OUT/result.json" \
  2>&1 | tee "$OUT/run.log"
STATUS=${PIPESTATUS[0]}
set -e
uptime >"$OUT/load_after.txt"
free -h >"$OUT/memory_after.txt"

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.log"
pgrep -af '[r]un_s100_d6_clean_rebuild.py|[r]un_s100_bpu_ab.py|[r]un_s100_d4_official_smoke.py|[h]rt_model_exec' >"$OUT/residual_processes.txt" || true
if grep -q . "$OUT/residual_processes.txt"; then
  printf 'ERROR: unexpected D6 residual process\n' >&2
  cat "$OUT/residual_processes.txt" >&2
  exit 1
fi
if [ "$STATUS" -ne 0 ]; then
  printf 'ERROR: D6 clean rebuild failed with status=%s\n' "$STATUS" >&2
  exit "$STATUS"
fi

printf 'S100_D6_CLEAN_REBUILD_PASS evidence=%s\n' "$OUT/result.json"
