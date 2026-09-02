#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-static}"
case "$MODE" in
  static) MOTION_STEP=0.0 ;;
  motion) MOTION_STEP=0.04 ;;
  *) printf 'usage: %s [static|motion]\n' "$0" >&2; exit 2 ;;
esac

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
INSTALL="$ROOT/install/kiss_icp-v1.3.0"
BIN="$INSTALL/lib/kiss_icp/kiss_icp_node"
STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
OUT="$ROOT/evidence/d2_kiss_icp_${MODE}/$STAMP"
NODE_PID=""

cleanup() {
  if [ -n "$NODE_PID" ] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill -INT "$NODE_PID" 2>/dev/null || true
    for _ in $(seq 1 50); do
      kill -0 "$NODE_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -TERM "$NODE_PID" 2>/dev/null || true
    wait "$NODE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

source "$ROOT/scripts/env_s100.sh" >/dev/null
export LD_LIBRARY_PATH="$INSTALL/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
bash "$ROOT/scripts/verify_s100_safety.sh"
test -x "$BIN"
mkdir -p "$OUT"

"$BIN" --ros-args \
  -r __ns:=/roboto_upgrade \
  -r pointcloud_topic:=/roboto_upgrade/synthetic_points \
  -p publish_debug_clouds:=false \
  -p publish_odom_tf:=false \
  -p data.deskew:=false \
  -p data.max_range:=30.0 \
  -p data.min_range:=0.1 \
  -p mapping.voxel_size:=0.25 \
  -p adaptive_threshold.min_motion_th:=0.01 \
  > "$OUT/kiss_icp.log" 2>&1 &
NODE_PID=$!
sleep 2
kill -0 "$NODE_PID"

python3 "$ROOT/probes/kiss_icp_synthetic_probe.py" \
  --output "$OUT/result.json" --motion-step-m "$MOTION_STEP"
cleanup
NODE_PID=""

python3 - "$OUT/result.json" "$MODE" "$OUT/cross_board.json" <<'PY'
import json
import pathlib
import sys

result_path = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
output_path = pathlib.Path(sys.argv[3])
result = json.loads(result_path.read_text(encoding="utf-8"))
checks = {
    "algorithm_pass": result.get("result") == "PASS",
    "same_fixture": result.get("fixture_xyz_sha256") == "c0a46df19753a67db4be24e4ef842daeaa06ad069f4e44344c048d733d7972e3",
    "same_frame_count": result.get("frames_sent") == 36,
    "enough_odometry": result.get("odometry_frames", 0) >= 30,
}
if mode == "static":
    checks["stationary_lte_2cm"] = result.get("max_relative_displacement_m", 1.0) <= 0.02
else:
    trajectory = result.get("trajectory") or {}
    checks["final_error_lte_10cm"] = trajectory.get("final_x_error_m", 1.0) <= 0.10
    checks["rmse_lte_8cm"] = trajectory.get("x_rmse_m", 1.0) <= 0.08
payload = {
    "schema_version": 1,
    "result": "PASS" if all(checks.values()) else "FAIL",
    "mode": mode,
    "checks": checks,
    "device_access": False,
    "tf_authority": False,
    "control_output": False,
}
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.txt"
if pgrep -ax kiss_icp_node > "$OUT/residuals.txt"; then
  printf 'ERROR: residual kiss_icp_node detected\n' >&2
  exit 1
fi
printf '%s\n' "$OUT"
