#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
COMMON=/home/sunrise/workspaces/new_project

test -f "$COMMON/README.md"
test -f "$COMMON/env.sh"
source "$COMMON/env.sh"
test "${ROS_DOMAIN_ID:-}" = 42
test "$ROOT" = /home/sunrise/workspaces/new_project/roboto_origin_s100

mkdir -p \
  "$ROOT/src" "$ROOT/build" "$ROOT/install" "$ROOT/logs" \
  "$ROOT/models" "$ROOT/data" "$ROOT/evidence" "$ROOT/config" \
  "$ROOT/inventory" "$ROOT/scripts" "$ROOT/probes" \
  "$ROOT/third_party/archives" "$ROOT/third_party/runtime"

chmod u+x "$ROOT/scripts/"*.sh
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh" >/dev/null

python3 - "$ROOT/evidence/d1_bootstrap_receipt.json" <<'PY'
import datetime
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

root = pathlib.Path("/home/sunrise/workspaces/new_project/roboto_origin_s100")
output = pathlib.Path(sys.argv[1])

def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

required = ["src", "build", "install", "logs", "models", "data", "evidence"]
payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "status": "PASS",
    "hostname": platform.node(),
    "machine": platform.machine(),
    "project_root": str(root),
    "required_directories": {name: (root / name).is_dir() for name in required},
    "ros_domain_id": 42,
    "port_range": [9140, 9199],
    "common_readme_sha256": sha256(root.parent / "README.md"),
    "common_env_sha256": sha256(root.parent / "env.sh"),
    "system_install_performed": False,
    "network_changed": False,
    "service_changed": False,
    "device_access": False,
    "rollback": "stop project processes and do not source project overlay",
    "git_head": subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    ).stdout.strip() or None,
}
payload["status"] = "PASS" if all(payload["required_directories"].values()) else "FAIL"
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["status"] == "PASS" else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
