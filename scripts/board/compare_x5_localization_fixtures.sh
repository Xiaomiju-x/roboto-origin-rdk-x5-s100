#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project
UPGRADE="$ROOT/src/roboto_origin_upgrade"
OUTPUT="$ROOT/logs/roboto_origin_upgrade/localization_fixture_comparison.json"

cd "$ROOT"
source "$ROOT/env.sh"
set +u
source /opt/tros/humble/setup.bash
set -u
test "${ROS_DOMAIN_ID:-}" = 42
mkdir -p "$(dirname "$OUTPUT")"

python3 - \
  "$ROOT/roboto_origin/probes/localization_synthetic_offline.py" \
  "$UPGRADE/probes/kiss_icp_synthetic_probe.py" \
  "$OUTPUT" <<'PY'
import hashlib, importlib.util, json, pathlib, sys
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
official = load("official_fast_lio_fixture", sys.argv[1])
candidate = load("candidate_kiss_fixture", sys.argv[2])
left = official.room_points()
right = candidate.make_asymmetric_room()
def describe(value):
    return {
        "shape": list(value.shape),
        "dtype": str(value.dtype),
        "sha256": hashlib.sha256(value.tobytes(order="C")).hexdigest(),
    }
same = left.shape == right.shape and left.dtype == right.dtype and left.tobytes() == right.tobytes()
payload = {
    "schema_version": 1,
    "scope": "in-memory synthetic XYZ fixture comparison; no node or device",
    "official_fast_lio_fixture": describe(left),
    "kiss_icp_fixture": describe(right),
    "byte_identical": same,
    "result": "PASS" if same else "FAIL",
}
pathlib.Path(sys.argv[3]).write_text(json.dumps(payload, indent=2) + "\n")
print(json.dumps(payload))
raise SystemExit(0 if same else 1)
PY

printf '%s\n' "$OUTPUT"
