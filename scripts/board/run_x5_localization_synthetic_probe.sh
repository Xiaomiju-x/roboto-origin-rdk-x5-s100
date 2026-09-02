#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
evidence_root="$project_root/evidence/localization_synthetic"
timestamp="$(date --iso-8601=seconds | tr ':' '-')"
run_dir="$evidence_root/$timestamp"
config="$project_root/config/localization_offline_mapping.yaml"
probe="$project_root/probes/localization_synthetic_offline.py"
declare -a owned_pids=()

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
export ROS_DOMAIN_ID=42

localization_prefix="$(ros2 pkg prefix robots_localization)"
localization_bin="$localization_prefix/lib/robots_localization/robots_localization_node"

cleanup() {
  local attempt alive pid
  if [ -n "${localization_pid:-}" ]; then
    kill -INT "$localization_pid" 2>/dev/null || true
  fi
  if [ -n "${probe_pid:-}" ]; then
    kill -TERM "$probe_pid" 2>/dev/null || true
  fi
  for attempt in $(seq 1 30); do
    alive=0
    for pid in "${owned_pids[@]:-}"; do
      kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${owned_pids[@]:-}"; do
    kill -KILL "$pid" 2>/dev/null || true
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
for required in "$config" "$probe" "$localization_bin"; do
  if [ ! -f "$required" ]; then
    printf 'ERROR: required file missing: %s\n' "$required" >&2
    exit 2
  fi
done

mkdir -p "$run_dir"
"$localization_bin" \
  --ros-args -r __ns:=/roboto_offline --params-file "$config" \
  >"$run_dir/localization.log" 2>&1 &
localization_pid="$!"
owned_pids+=("$localization_pid")

python3 "$probe" --output "$run_dir/result.json" --duration 8 \
  >"$run_dir/probe.log" 2>&1 &
probe_pid="$!"
owned_pids+=("$probe_pid")

probe_status=0
wait "$probe_pid" || probe_status="$?"

kill -INT "$localization_pid" 2>/dev/null || true
graceful_shutdown=true
for attempt in $(seq 1 100); do
  if ! kill -0 "$localization_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if kill -0 "$localization_pid" 2>/dev/null; then
  graceful_shutdown=false
  kill -KILL "$localization_pid" 2>/dev/null || true
fi
localization_status=0
wait "$localization_pid" || localization_status="$?"
if [ "$localization_status" -ne 0 ] || [ "$graceful_shutdown" != "true" ]; then
  probe_status=1
fi

python3 - "$run_dir/result.json" "$run_dir/safety.json" "$localization_status" "$graceful_shutdown" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
safety_path = Path(sys.argv[2])
localization_status = int(sys.argv[3])
graceful_shutdown = sys.argv[4].lower() == "true"
services = [
    "embodied_brain.service",
    "cockpit_bridge.service",
    "eb_sensor_watchdog.service",
    "xrd-v6-8890.service",
]
states = {}
for service in services:
    process = subprocess.run(
        ["systemctl", "is-active", service], check=False, capture_output=True, text=True
    )
    states[service] = process.stdout.strip() or "unknown"
can_process = subprocess.run(
    ["ip", "-brief", "link", "show", "can0"], check=False, capture_output=True, text=True
)
safety = {
    "ros_domain_id": 42,
    "can0": can_process.stdout.strip(),
    "frozen_services": states,
    "sensor_drivers_launched": False,
    "device_nodes_launched": False,
    "robot_control_nodes_launched": False,
    "pcd_save_enabled": False,
}
safety_path.write_text(json.dumps(safety, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if result_path.exists():
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["safety_evidence"] = safety
    result["shutdown"] = {
        "signal": "SIGINT",
        "exit_code": localization_status,
        "graceful": graceful_shutdown and localization_status == 0,
    }
    if not result["shutdown"]["graceful"]:
        result["status"] = "FAIL"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf '%s\n' "$run_dir" >"$evidence_root/LATEST"
printf 'RESULT_DIR=%s\n' "$run_dir"
cat "$run_dir/result.json" 2>/dev/null || true
exit "$probe_status"
