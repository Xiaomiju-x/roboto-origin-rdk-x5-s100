#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project
UPGRADE="$ROOT/src/roboto_origin_upgrade"
STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
EVIDENCE="$ROOT/logs/roboto_origin_upgrade/runtime/$STAMP"
MODEL="$UPGRADE/models/tiny_temporal_occ_flow.onnx"
VECTORS="$UPGRADE/models/x5_probe_input.npz"

cd "$ROOT"
source "$ROOT/env.sh"
test "${ROS_DOMAIN_ID:-}" = 42
mkdir -p "$EVIDENCE"

python3 "$UPGRADE/probes/x5_upgrade_onnx_probe.py" \
  --model "$MODEL" \
  --vectors "$VECTORS" \
  --output "$EVIDENCE/temporal_onnx.json"

PYTHONPATH="$UPGRADE/src" python3 "$UPGRADE/scripts/run_trust_guard_ab.py" \
  --repo "$UPGRADE" \
  --output "$EVIDENCE/trust_guard.json"

python3 - "$EVIDENCE/safety.json" <<'PY'
import json, pathlib, subprocess, sys
output = pathlib.Path(sys.argv[1])
def run(command):
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    return {"returncode": completed.returncode, "stdout": completed.stdout.strip()}
payload = {
    "ros_domain_id": 42,
    "ssh": run(["systemctl", "is-active", "ssh.service"]),
    "vnc": run(["systemctl", "is-active", "x11vnc.service"]),
    "can0": run(["ip", "-details", "link", "show", "can0"]),
    "frozen_services": {
        name: run(["systemctl", "is-active", name])
        for name in ("embodied_brain.service", "cockpit_bridge.service", "eb_sensor_watchdog.service", "xrd-v6-8890.service")
    },
    "residuals": run(["pgrep", "-af", "tiny_temporal_occ_flow|run_trust_guard_ab|x5_upgrade_onnx_probe"]),
}
output.write_text(json.dumps(payload, indent=2) + "\n")
PY

grep -q 'state DOWN' "$EVIDENCE/safety.json"
grep -q 'STOPPED' "$EVIDENCE/safety.json"
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"])' "$EVIDENCE/temporal_onnx.json")" = PASS
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"])' "$EVIDENCE/trust_guard.json")" = PASS
printf '%s\n' "$EVIDENCE"
