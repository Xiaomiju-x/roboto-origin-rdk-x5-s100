#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
OUT="$ROOT/evidence/d2_upgrade_u1_u2/$STAMP"
MODEL="$ROOT/models/upgrade/tiny_temporal_occ_flow.onnx"
VECTORS="$ROOT/data/fixtures/x5_probe_input.npz"
mkdir -p "$OUT"

test "$(sha256sum "$MODEL" | awk '{print $1}')" = \
  2ce2207bf18435b71153cd8e7e62753a2d474609ba597abc68e3f164f8f48c03
test "$(sha256sum "$VECTORS" | awk '{print $1}')" = \
  e034065f32a8dc74c9337e184b644787366eb740cd9929d54fd7e9a9c50d4dc6

PYTHONNOUSERSITE=1 PYTHONPATH="$ROOT/third_party/python" \
  python3 "$ROOT/probes/x5_upgrade_onnx_probe.py" \
  --model "$MODEL" --vectors "$VECTORS" \
  --output "$OUT/temporal_onnx.json" --repeats 100

PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/run_trust_guard_ab.py" \
  --repo "$ROOT" --output "$OUT/trust_guard.json"

python3 - "$OUT/temporal_onnx.json" "$OUT/trust_guard.json" "$OUT/summary.json" <<'PY'
import json
import pathlib
import sys

temporal_path, guard_path, output_path = map(pathlib.Path, sys.argv[1:])
temporal = json.loads(temporal_path.read_text(encoding="utf-8"))
guard = json.loads(guard_path.read_text(encoding="utf-8"))
checks = {
    "u1_temporal_pass": temporal.get("result") == "PASS",
    "u1_nonzero_input": temporal.get("input_nonzero") is True,
    "u1_cpu_provider": temporal.get("onnxruntime", {}).get("providers", [None])[0] == "CPUExecutionProvider",
    "u1_same_model": temporal.get("model", {}).get("sha256") == "2ce2207bf18435b71153cd8e7e62753a2d474609ba597abc68e3f164f8f48c03",
    "u1_same_fixture": temporal.get("vectors", {}).get("sha256") == "e034065f32a8dc74c9337e184b644787366eb740cd9929d54fd7e9a9c50d4dc6",
    "u2_guard_pass": guard.get("result") == "PASS",
    "u2_all_faults_detected": guard.get("criteria", {}).get("candidate_all_faults_detected") is True,
    "u2_finite": guard.get("criteria", {}).get("candidate_outputs_finite") is True,
}
payload = {
    "schema_version": 1,
    "scope": "S100 CPU reproduction of X5 U1 temporal BEV and U2 action guard",
    "device_access": False,
    "control_output": False,
    "checks": checks,
    "result": "PASS" if all(checks.values()) else "FAIL",
}
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.txt"
printf '%s\n' "$OUT"
