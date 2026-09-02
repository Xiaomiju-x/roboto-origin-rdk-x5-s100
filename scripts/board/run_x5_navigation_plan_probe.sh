#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
evidence_root="$project_root/evidence/navigation_plan"
timestamp="$(date --iso-8601=seconds | tr ':' '-')"
run_dir="$evidence_root/$timestamp"
map_yaml="${ROBOTO_NAV_MAP_YAML:-$project_root/evidence/x5/navigation_offline/20260828T223859+0800/map_ikdtree_x5_offline.yaml}"
planner_params="$project_root/config/navigation_offline_planner.yaml"
adapter_params="$project_root/deps/install/official_navigation/nav2_localization_adapter/share/nav2_localization_adapter/config/adapter_params.yaml"
probe="$project_root/probes/navigation_plan_offline.py"
declare -a owned_pids=()

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
export ROS_DOMAIN_ID=42

adapter_bin="$(ros2 pkg prefix nav2_localization_adapter)/lib/nav2_localization_adapter/nav2_localization_adapter_node"
map_server_bin="$(ros2 pkg prefix nav2_map_server)/lib/nav2_map_server/map_server"
planner_server_bin="$(ros2 pkg prefix nav2_planner)/lib/nav2_planner/planner_server"
lifecycle_manager_bin="$(ros2 pkg prefix nav2_lifecycle_manager)/lib/nav2_lifecycle_manager/lifecycle_manager"

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

for required in \
  "$map_yaml" "$planner_params" "$adapter_params" "$probe" \
  "$adapter_bin" "$map_server_bin" "$planner_server_bin" "$lifecycle_manager_bin"; do
  if [ ! -f "$required" ]; then
    printf 'ERROR: required file missing: %s\n' "$required" >&2
    exit 2
  fi
done

mkdir -p "$run_dir"

"$adapter_bin" \
  --ros-args --params-file "$adapter_params" \
  >"$run_dir/adapter.log" 2>&1 &
owned_pids+=("$!")

python3 "$probe" --output "$run_dir/result.json" --timeout 45 \
  >"$run_dir/probe.log" 2>&1 &
probe_pid="$!"
owned_pids+=("$probe_pid")

"$map_server_bin" \
  --ros-args -r __node:=map_server -p "yaml_filename:=$map_yaml" -p use_sim_time:=false \
  >"$run_dir/map_server.log" 2>&1 &
owned_pids+=("$!")

"$planner_server_bin" \
  --ros-args --params-file "$planner_params" \
  >"$run_dir/planner_server.log" 2>&1 &
owned_pids+=("$!")

"$lifecycle_manager_bin" \
  --ros-args -r __node:=lifecycle_manager_offline --params-file "$planner_params" \
  >"$run_dir/lifecycle_manager.log" 2>&1 &
owned_pids+=("$!")

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
        ["systemctl", "is-active", service],
        check=False,
        capture_output=True,
        text=True,
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
