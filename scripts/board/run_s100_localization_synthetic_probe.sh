#!/usr/bin/env bash

set -eo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
EVIDENCE_ROOT="$ROOT/evidence/d2_fastlio_synthetic"
TIMESTAMP="$(date --iso-8601=seconds | tr ':' '-')"
RUN_DIR="$EVIDENCE_ROOT/$TIMESTAMP"
CONFIG="$ROOT/config/localization_offline_mapping.yaml"
PROBE="$ROOT/probes/localization_synthetic_offline.py"
LOCALIZATION_BIN="$ROOT/install/robots_localization/lib/robots_localization/robots_localization_node"
declare -a OWNED_PIDS=()

source "$ROOT/scripts/env_s100_fastlio.sh" >/dev/null
export OPENBLAS_NUM_THREADS=1
bash "$ROOT/scripts/verify_s100_safety.sh"

cleanup() {
  local attempt alive pid
  if [ -n "${LOCALIZATION_PID:-}" ]; then
    kill -INT "$LOCALIZATION_PID" 2>/dev/null || true
  fi
  if [ -n "${PROBE_PID:-}" ]; then
    kill -TERM "$PROBE_PID" 2>/dev/null || true
  fi
  for attempt in $(seq 1 50); do
    alive=0
    for pid in "${OWNED_PIDS[@]:-}"; do
      kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${OWNED_PIDS[@]:-}"; do
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

for required in "$CONFIG" "$PROBE" "$LOCALIZATION_BIN"; do
  if [ ! -f "$required" ]; then
    printf 'ERROR: required file missing: %s\n' "$required" >&2
    exit 2
  fi
done

mkdir -p "$RUN_DIR"
cat /proc/loadavg >"$RUN_DIR/load_before.txt"
sha256sum "$CONFIG" "$PROBE" "$LOCALIZATION_BIN" >"$RUN_DIR/input_sha256.txt"

"$LOCALIZATION_BIN" \
  --ros-args -r __ns:=/roboto_offline --params-file "$CONFIG" \
  >"$RUN_DIR/localization.log" 2>&1 &
LOCALIZATION_PID="$!"
OWNED_PIDS+=("$LOCALIZATION_PID")
sleep 1
if ! kill -0 "$LOCALIZATION_PID" 2>/dev/null; then
  printf 'ERROR: localization node exited during startup\n' >&2
  tail -80 "$RUN_DIR/localization.log" >&2 || true
  exit 1
fi

python3 "$PROBE" --output "$RUN_DIR/result.json" --duration 8 \
  >"$RUN_DIR/probe.log" 2>&1 &
PROBE_PID="$!"
OWNED_PIDS+=("$PROBE_PID")

PROBE_STATUS=0
wait "$PROBE_PID" || PROBE_STATUS="$?"

kill -INT "$LOCALIZATION_PID" 2>/dev/null || true
GRACEFUL_SHUTDOWN=true
for attempt in $(seq 1 100); do
  if ! kill -0 "$LOCALIZATION_PID" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if kill -0 "$LOCALIZATION_PID" 2>/dev/null; then
  GRACEFUL_SHUTDOWN=false
  kill -KILL "$LOCALIZATION_PID" 2>/dev/null || true
fi
LOCALIZATION_STATUS=0
wait "$LOCALIZATION_PID" || LOCALIZATION_STATUS="$?"
if [ "$LOCALIZATION_STATUS" -ne 0 ] || [ "$GRACEFUL_SHUTDOWN" != true ]; then
  PROBE_STATUS=1
fi

cat /proc/loadavg >"$RUN_DIR/load_after.txt"
bash "$ROOT/scripts/verify_s100_safety.sh" >"$RUN_DIR/safety_after.txt"

python3 - "$RUN_DIR/result.json" "$LOCALIZATION_STATUS" "$GRACEFUL_SHUTDOWN" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
exit_code = int(sys.argv[2])
graceful = sys.argv[3].lower() == "true" and exit_code == 0
payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "FAIL"}
payload["platform"] = "RDK S100/S100E 80 TOPS"
payload["ros_domain_id"] = 42
payload["source_lock"] = {
    "roboparty_navigation_commit": "d6ab9913599672af78422eb2e89a16c0460a8009",
    "robots_localization_commit": "dc4375a707dc4546ffad3a631f4e0d7c211c065b",
}
payload["safety"] = {
    "sensor_drivers_launched": False,
    "device_nodes_accessed": False,
    "robot_control_topics_used": False,
    "pcd_save_enabled": False,
}
payload["shutdown"] = {"signal": "SIGINT", "exit_code": exit_code, "graceful": graceful}
if not graceful:
    payload["status"] = "FAIL"
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf '%s\n' "$RUN_DIR" >"$EVIDENCE_ROOT/LATEST"
printf 'RESULT_DIR=%s\n' "$RUN_DIR"
cat "$RUN_DIR/result.json" 2>/dev/null || true
exit "$PROBE_STATUS"
