#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
evidence_root="$project_root/evidence/localization_shutdown_debug"
timestamp="$(date --iso-8601=seconds | tr ':' '-')"
run_dir="$evidence_root/$timestamp"
config="$project_root/config/localization_offline_mapping.yaml"
probe="$project_root/probes/localization_synthetic_offline.py"

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
export ROS_DOMAIN_ID=42

localization_prefix="$(ros2 pkg prefix robots_localization)"
localization_bin="$localization_prefix/lib/robots_localization/robots_localization_node"
gdb_pid=""
inferior_pid=""
probe_pid=""

cleanup() {
  [ -z "$probe_pid" ] || kill -TERM "$probe_pid" 2>/dev/null || true
  [ -z "$inferior_pid" ] || kill -KILL "$inferior_pid" 2>/dev/null || true
  [ -z "$gdb_pid" ] || kill -KILL "$gdb_pid" 2>/dev/null || true
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
for required in "$config" "$probe" "$localization_bin"; do
  if [ ! -f "$required" ]; then
    printf 'ERROR: required file missing: %s\n' "$required" >&2
    exit 2
  fi
done

mkdir -p "$run_dir"
gdb --quiet --batch \
  -ex 'set pagination off' \
  -ex 'handle SIGINT nostop noprint pass' \
  -ex run \
  -ex 'thread apply all bt full' \
  --args "$localization_bin" \
    --ros-args -r __ns:=/roboto_offline --params-file "$config" \
  >"$run_dir/gdb.log" 2>&1 &
gdb_pid="$!"

for attempt in $(seq 1 100); do
  inferior_pid="$(pgrep -P "$gdb_pid" 2>/dev/null | awk 'NR == 1 { print; exit }' || true)"
  [ -n "$inferior_pid" ] && break
  sleep 0.1
done
if [ -z "$inferior_pid" ]; then
  printf 'ERROR: gdb inferior did not start\n' >&2
  exit 1
fi

python3 "$probe" --output "$run_dir/probe_result.json" --duration 5 \
  >"$run_dir/probe.log" 2>&1 &
probe_pid="$!"
wait "$probe_pid" || true
probe_pid=""

kill -INT "$inferior_pid" 2>/dev/null || true
for attempt in $(seq 1 300); do
  if ! kill -0 "$gdb_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if kill -0 "$gdb_pid" 2>/dev/null; then
  printf 'ERROR: gdb did not finish after shutdown signal\n' >&2
  exit 1
fi
wait "$gdb_pid" || true
gdb_pid=""
inferior_pid=""

printf '%s\n' "$run_dir" >"$evidence_root/LATEST"
printf 'RESULT_DIR=%s\n' "$run_dir"
grep -E 'Program received signal|#0 |#1 |#2 |#3 |#4 |#5 ' "$run_dir/gdb.log" || true
