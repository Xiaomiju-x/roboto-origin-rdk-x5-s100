#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
NAV2="$ROOT/install/nav2_bundle/opt/ros/humble"
ADAPTER="$ROOT/install/nav2_adapter"
MAP_YAML="$(find "$ROOT/evidence/d2_pcd_map" -mindepth 2 -maxdepth 2 -name map_ikdtree_x5_offline.yaml -print | sort | tail -1)"
PARAMS="$ROOT/config/navigation_offline_planner.yaml"
ADAPTER_PARAMS="$ADAPTER/share/nav2_localization_adapter/config/adapter_params.yaml"
STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
OUT="$ROOT/evidence/d5_nav2_multigoal/$STAMP"
declare -a PIDS=()

cleanup() {
  local pid attempt alive
  for pid in "${PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for attempt in $(seq 1 30); do
    alive=0
    for pid in "${PIDS[@]:-}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${PIDS[@]:-}"; do
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

source "$ROOT/scripts/env_s100_nav2.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"
test -f "$MAP_YAML"
test -f "$PARAMS"
test -f "$ADAPTER_PARAMS"
mkdir -p "$OUT"

adapter="$ADAPTER/lib/nav2_localization_adapter/nav2_localization_adapter_node"
map_server="$NAV2/lib/nav2_map_server/map_server"
planner="$NAV2/lib/nav2_planner/planner_server"
lifecycle="$NAV2/lib/nav2_lifecycle_manager/lifecycle_manager"
for executable in "$adapter" "$map_server" "$planner" "$lifecycle"; do test -x "$executable"; done

"$adapter" --ros-args --params-file "$ADAPTER_PARAMS" >"$OUT/adapter.log" 2>&1 & PIDS+=("$!")
"$map_server" --ros-args -r __node:=map_server -p "yaml_filename:=$MAP_YAML" -p use_sim_time:=false >"$OUT/map_server.log" 2>&1 & PIDS+=("$!")
"$planner" --ros-args --params-file "$PARAMS" >"$OUT/planner_server.log" 2>&1 & PIDS+=("$!")
"$lifecycle" --ros-args -r __node:=lifecycle_manager_offline --params-file "$PARAMS" >"$OUT/lifecycle_manager.log" 2>&1 & PIDS+=("$!")

python3 "$ROOT/probes/navigation_multi_goal_offline.py" \
  --output "$OUT/result.json" \
  --capture "$OUT/cmd_vel_capture.jsonl" \
  --goal-count 20 --timeout 45 \
  >"$OUT/probe.log" 2>&1
cleanup
PIDS=()

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.log"
if pgrep -af 'nav2_localization_adapter_node|map_server|planner_server|lifecycle_manager' >"$OUT/residuals.txt"; then
  printf 'ERROR: residual Nav2 process detected\n' >&2
  exit 1
fi
printf 'S100_D5_NAV2_MULTIGOAL_PASS evidence=%s\n' "$OUT/result.json"
