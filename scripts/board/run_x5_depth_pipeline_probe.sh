#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
evidence_root="$project_root/evidence/depth_pipeline"
timestamp="$(date --iso-8601=seconds | tr ':' '-')"
run_dir="$evidence_root/$timestamp"
probe="$project_root/probes/depth_pipeline_offline.py"
declare -a owned_pids=()

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
export ROS_DOMAIN_ID=42

camera_prefix="$(ros2 pkg prefix camera)"
depth_node_bin="$camera_prefix/lib/camera/depth_node"
depth_config="$camera_prefix/share/camera/configs/parkour.yaml"
model_dir="$camera_prefix/share/camera/models"

cleanup() {
  local attempt
  local alive
  local pid
  for pid in "${owned_pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for attempt in $(seq 1 20); do
    alive=0
    for pid in "${owned_pids[@]:-}"; do
      if kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${owned_pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${owned_pids[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

if [ "$ROS_DOMAIN_ID" != "42" ]; then
  printf 'ERROR: ROS_DOMAIN_ID must remain 42\n' >&2
  exit 2
fi
if ! ip link show can0 2>/dev/null | grep -q 'state DOWN'; then
  printf 'ERROR: can0 is absent or not DOWN\n' >&2
  exit 2
fi
for service in embodied_brain.service cockpit_bridge.service eb_sensor_watchdog.service xrd-v6-8890.service; do
  if [ "$(systemctl is-active "$service" 2>/dev/null || true)" != "inactive" ]; then
    printf 'ERROR: frozen service unexpectedly active: %s\n' "$service" >&2
    exit 2
  fi
done
for required in "$probe" "$depth_node_bin" "$depth_config" "$model_dir/encoder.onnx"; do
  if [ ! -f "$required" ]; then
    printf 'ERROR: required file missing: %s\n' "$required" >&2
    exit 2
  fi
done

mkdir -p "$run_dir"
"$depth_node_bin" \
  --ros-args --params-file "$depth_config" -p "model_dir:=$model_dir" \
  >"$run_dir/depth_node.log" 2>&1 &
owned_pids+=("$!")

python3 "$probe" --output "$run_dir/result.json" \
  >"$run_dir/probe.log" 2>&1 &
probe_pid="$!"
owned_pids+=("$probe_pid")

probe_status=0
wait "$probe_pid" || probe_status="$?"

python3 - "$run_dir/result.json" "$run_dir/safety.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
safety_path = Path(sys.argv[2])
services = [
    "embodied_brain.service",
    "cockpit_bridge.service",
    "eb_sensor_watchdog.service",
    "xrd-v6-8890.service",
]
service_states = {}
for service in services:
    process = subprocess.run(
        ["systemctl", "is-active", service], check=False, capture_output=True, text=True
    )
    service_states[service] = process.stdout.strip() or "unknown"
can_process = subprocess.run(
    ["ip", "-brief", "link", "show", "can0"],
    check=False,
    capture_output=True,
    text=True,
)
safety = {
    "ros_domain_id": 42,
    "can0": can_process.stdout.strip(),
    "frozen_services": service_states,
    "camera_driver_launched": False,
    "device_nodes_launched": False,
    "robot_control_nodes_launched": False,
}
safety_path.write_text(json.dumps(safety, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if result_path.exists():
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["safety_evidence"] = safety
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf '%s\n' "$run_dir" >"$evidence_root/LATEST"
printf 'RESULT_DIR=%s\n' "$run_dir"
cat "$run_dir/result.json" 2>/dev/null || true
exit "$probe_status"
