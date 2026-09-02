#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project
UPGRADE="$ROOT/src/roboto_origin_upgrade"
INSTALL="$ROOT/install/roboto_origin_upgrade/kiss_icp_v130"
MODE="${1:-static}"
case "$MODE" in
  static) MOTION_STEP=0.0; EVIDENCE_GROUP=kiss_icp ;;
  motion) MOTION_STEP=0.04; EVIDENCE_GROUP=kiss_icp_motion ;;
  *) echo "usage: $0 [static|motion]" >&2; exit 2 ;;
esac
STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
EVIDENCE="$ROOT/logs/roboto_origin_upgrade/$EVIDENCE_GROUP/$STAMP"
NODE_LOG="$EVIDENCE/kiss_icp.log"
RESULT="$EVIDENCE/result.json"
SAFETY="$EVIDENCE/safety.json"
SAFETY_AFTER="$EVIDENCE/safety_after.json"
LIFECYCLE="$EVIDENCE/lifecycle.json"
NODE_PID=""
FORCED_KILL=0

cleanup() {
  if [[ -n "$NODE_PID" ]] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill -INT "$NODE_PID" 2>/dev/null || true
    for _ in $(seq 1 50); do
      kill -0 "$NODE_PID" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$NODE_PID" 2>/dev/null; then
      kill -TERM "$NODE_PID" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$NODE_PID" 2>/dev/null || break
        sleep 0.1
      done
    fi
    if kill -0 "$NODE_PID" 2>/dev/null; then
      FORCED_KILL=1
      kill -KILL "$NODE_PID" 2>/dev/null || true
    fi
    wait "$NODE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$ROOT"
source "$ROOT/env.sh"
set +u
source /opt/tros/humble/setup.bash
source "$INSTALL/setup.bash"
set -u
test "${ROS_DOMAIN_ID:-}" = 42
mkdir -p "$EVIDENCE"

python3 - "$SAFETY" <<'PY'
import json, pathlib, subprocess, sys
output = pathlib.Path(sys.argv[1])
def run(command):
    return subprocess.run(command, text=True, capture_output=True, check=False).stdout.strip()
payload = {
    "ros_domain_id": 42,
    "can0": run(["ip", "-details", "link", "show", "can0"]),
    "frozen_services": {
        name: run(["systemctl", "is-active", name])
        for name in ("embodied_brain.service", "cockpit_bridge.service", "eb_sensor_watchdog.service", "xrd-v6-8890.service")
    },
}
output.write_text(json.dumps(payload, indent=2) + "\n")
PY
grep -q 'state DOWN' "$SAFETY"
grep -q 'STOPPED' "$SAFETY"

"$INSTALL/kiss_icp/lib/kiss_icp/kiss_icp_node" --ros-args \
  -r __ns:=/roboto_upgrade \
  -r pointcloud_topic:=/roboto_upgrade/synthetic_points \
  -p publish_debug_clouds:=false \
  -p publish_odom_tf:=false \
  -p data.deskew:=false \
  -p data.max_range:=30.0 \
  -p data.min_range:=0.1 \
  -p mapping.voxel_size:=0.25 \
  -p adaptive_threshold.min_motion_th:=0.01 \
  >"$NODE_LOG" 2>&1 &
NODE_PID=$!
sleep 2
kill -0 "$NODE_PID"

python3 "$UPGRADE/probes/kiss_icp_synthetic_probe.py" --output "$RESULT" --motion-step-m "$MOTION_STEP"
cleanup
NODE_PID=""

python3 - "$LIFECYCLE" "$FORCED_KILL" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "direct_executable": True,
    "forced_kill_required": bool(int(sys.argv[2])),
    "residual_processes": False,
}, indent=2) + "\n")
PY

if pgrep -ax kiss_icp_node >"$EVIDENCE/residuals.txt"; then
  python3 - "$LIFECYCLE" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text())
payload["residual_processes"] = True
path.write_text(json.dumps(payload, indent=2) + "\n")
PY
  echo "Residual process detected" >&2
  exit 1
fi
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"])' "$RESULT")" = PASS

python3 - "$SAFETY_AFTER" <<'PY'
import json, pathlib, subprocess, sys
output = pathlib.Path(sys.argv[1])
def run(command):
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    return {"returncode": completed.returncode, "stdout": completed.stdout.strip()}
services = {
    name: run(["systemctl", "is-active", name])
    for name in ("embodied_brain.service", "cockpit_bridge.service", "eb_sensor_watchdog.service", "xrd-v6-8890.service")
}
can0 = run(["ip", "-details", "link", "show", "can0"])
ssh = run(["systemctl", "is-active", "ssh.service"])
vnc = run(["systemctl", "is-active", "x11vnc.service"])
residuals = run(["pgrep", "-x", "kiss_icp_node"])
checks = {
    "ros_domain_42": True,
    "ssh_active": ssh["stdout"] == "active",
    "vnc_active": vnc["stdout"] == "active",
    "can_down_stopped": "state DOWN" in can0["stdout"] and "STOPPED" in can0["stdout"],
    "frozen_services_inactive": all(item["stdout"] == "inactive" for item in services.values()),
    "no_residual_process": residuals["returncode"] == 1,
}
payload = {"checks": checks, "ssh": ssh, "vnc": vnc, "can0": can0, "frozen_services": services, "residuals": residuals,
           "result": "PASS" if all(checks.values()) else "FAIL"}
output.write_text(json.dumps(payload, indent=2) + "\n")
PY
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"])' "$SAFETY_AFTER")" = PASS
printf '%s\n' "$EVIDENCE"
