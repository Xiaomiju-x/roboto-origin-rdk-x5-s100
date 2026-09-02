#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
export OPENBLAS_NUM_THREADS=1
bash "$ROOT/scripts/verify_s100_safety.sh"

STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
OUT="$ROOT/evidence/d2_pcd_map/$STAMP"
OFFICIAL="$ROOT/src/official/roboparty_navigation/nav2_localization_adapter/scripts/pcd2pgm.py"
PCD="$ROOT/data/fixtures/map_ikdtree.pcd"
SUMMARY="$OUT/pcd_to_pgm_summary.json"
mkdir -p "$OUT"

converter_hash="$(sha256sum "$OFFICIAL" | awk '{print $1}')"
if [ "$converter_hash" = \
    9a848dcf910cbb4cf2e5bbe62110bb651c1f7783b9f5881f2577c58149bc02e0 ]; then
  # The Windows transport checkout may carry CRLF. Normalize only the isolated
  # S100 copy to the byte-identical LF form that was executed on X5.
  sed -i 's/\r$//' "$OFFICIAL"
fi
test "$(sha256sum "$OFFICIAL" | awk '{print $1}')" = \
  5b84277671cf941cd0821bef20d53ae5a596355a9d64e2344e7da67cb0a6ccab
test "$(sha256sum "$PCD" | awk '{print $1}')" = \
  4443430c0ceced4effac6514e3dd743fd27411e1fe51862ed0ffdac2cf9612ca

PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
PYTHONPATH="$ROOT/third_party/open3d_python:$ROOT/third_party/python" \
  python3 "$ROOT/probes/pcd_to_pgm_offline.py" \
  --official-script "$OFFICIAL" \
  --input-pcd "$PCD" \
  --output-dir "$OUT" \
  --summary "$SUMMARY" \
  --map-name map_ikdtree_x5_offline \
  | tee "$OUT/probe.log"

python3 - "$SUMMARY" "$OUT/cross_board_comparison.json" <<'PY'
import json
import pathlib
import sys

summary_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
summary = json.loads(summary_path.read_text(encoding="utf-8"))
expected = {
    "input_sha256": "4443430c0ceced4effac6514e3dd743fd27411e1fe51862ed0ffdac2cf9612ca",
    "converter_sha256": "5b84277671cf941cd0821bef20d53ae5a596355a9d64e2344e7da67cb0a6ccab",
    "pgm_sha256": "66fae06990e90202375bfb64f904f63348de26b86e146723562335ae88b9d308",
    "yaml_sha256": "5a044dc91990f27f96e16e88ca34afe7f7c91fc24495df475e32f25f2adf6d9a",
    "cell_counts": {"free": 25435, "occupied": 7386, "unknown": 88747},
    "grid": [232, 524],
}
checks = {
    "probe_pass": summary.get("result") == "PASS",
    "no_device_access": summary.get("device_access") is False,
    "same_input": summary["input"]["sha256"] == expected["input_sha256"],
    "same_converter": summary["official_converter"]["sha256"] == expected["converter_sha256"],
    "same_pgm": summary["output"]["pgm"]["sha256"] == expected["pgm_sha256"],
    "same_yaml": summary["output"]["yaml"]["sha256"] == expected["yaml_sha256"],
    "same_cells": summary["output"]["cell_counts"] == expected["cell_counts"],
    "same_grid": [summary["output"]["width"], summary["output"]["height"]] == expected["grid"],
}
payload = {
    "schema_version": 1,
    "result": "PASS" if all(checks.values()) else "FAIL",
    "x5_expected": expected,
    "checks": checks,
}
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.txt"
printf '%s\n' "$OUT"
