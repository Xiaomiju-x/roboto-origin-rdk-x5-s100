#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
INSTALL="$ROOT/install/official_depth"
BIN="$INSTALL/lib/camera/depth_node"
CONFIG="$INSTALL/share/camera/configs/parkour.yaml"
MODEL_DIR="$INSTALL/share/camera/models"
STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
OUT="$ROOT/evidence/d2_depth_pipeline/$STAMP"
NODE_PID=""
PROBE_PID=""

cleanup() {
  local pid attempt
  for pid in "$PROBE_PID" "$NODE_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for attempt in $(seq 1 30); do
    local alive=0
    for pid in "$PROBE_PID" "$NODE_PID"; do
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "$PROBE_PID" "$NODE_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

source "$ROOT/scripts/env_s100.sh" >/dev/null
export LD_LIBRARY_PATH="$INSTALL/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
bash "$ROOT/scripts/verify_s100_safety.sh"
for required in "$BIN" "$CONFIG" "$MODEL_DIR/encoder.onnx" "$ROOT/probes/depth_pipeline_offline.py"; do
  test -f "$required"
done
mkdir -p "$OUT"

"$BIN" --ros-args --params-file "$CONFIG" -p "model_dir:=$MODEL_DIR" \
  > "$OUT/depth_node.log" 2>&1 &
NODE_PID=$!
sleep 2
kill -0 "$NODE_PID"

OPENBLAS_NUM_THREADS=1 python3 "$ROOT/probes/depth_pipeline_offline.py" \
  --output "$OUT/result.json" > "$OUT/probe.log" 2>&1 &
PROBE_PID=$!
probe_status=0
wait "$PROBE_PID" || probe_status=$?
PROBE_PID=""
cleanup
NODE_PID=""

python3 - "$OUT/result.json" "$ROOT/data/fixtures/x5_depth_reference.json" "$OUT/cross_board.json" <<'PY'
import json
import math
import pathlib
import sys

actual_path, expected_path, output_path = map(pathlib.Path, sys.argv[1:])
actual = json.loads(actual_path.read_text(encoding="utf-8"))
expected = json.loads(expected_path.read_text(encoding="utf-8"))
a = actual.get("first_fixed_output", {}).get("values", [])
b = expected.get("first_fixed_output", {}).get("values", [])
max_abs = max((abs(x - y) for x, y in zip(a, b)), default=math.inf)
checks = {
    "pipeline_pass": actual.get("status") == "PASS",
    "shape_match": len(a) == len(b) == 128,
    "fixed_output_match": max_abs <= 2e-5,
    "downsample_hash_match": actual.get("downsample_samples", [{}])[0].get("sha256_float32_le") == expected.get("downsample_samples", [{}])[0].get("sha256_float32_le"),
    "crop_hash_match": actual.get("crop_samples", [{}])[0].get("sha256_float32_le") == expected.get("crop_samples", [{}])[0].get("sha256_float32_le"),
}
payload = {
    "schema_version": 1,
    "result": "PASS" if all(checks.values()) else "FAIL",
    "checks": checks,
    "first_output_max_abs_error_vs_x5": max_abs,
    "device_access": False,
    "camera_driver_launched": False,
}
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

test "$probe_status" -eq 0
bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.txt"
if pgrep -ax depth_node > "$OUT/residuals.txt"; then
  printf 'ERROR: residual depth_node detected\n' >&2
  exit 1
fi
printf '%s\n' "$OUT"
