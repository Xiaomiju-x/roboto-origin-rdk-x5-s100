#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
OUTPUT="$ROOT/evidence/d2_summary.json"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

python3 - "$ROOT" "$OUTPUT" <<'PY'
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

root, output = map(pathlib.Path, sys.argv[1:])
evidence = root / "evidence"


def latest_file(group: str, filename: str) -> pathlib.Path:
    candidates = sorted(path for path in (evidence / group).glob("*") if path.is_dir())
    if not candidates:
        raise FileNotFoundError(f"no evidence directories for {group}")
    path = candidates[-1] / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


sources = {
    "official_onnx_cpu": latest_file("d2_cpu_onnx", "summary.json"),
    "official_depth_history": latest_file("d2_depth_pipeline", "result.json"),
    "official_pcd_to_pgm": latest_file("d2_pcd_map", "cross_board_comparison.json"),
    "official_nav2": latest_file("d2_nav2_plan", "result.json"),
    "official_fastlio2": latest_file("d2_fastlio_synthetic", "result.json"),
    "upgrade_u1_u2": latest_file("d2_upgrade_u1_u2", "summary.json"),
    "upgrade_u3_kiss_icp_static": latest_file("d2_kiss_icp_static", "result.json"),
    "upgrade_u3_kiss_icp_motion": latest_file("d2_kiss_icp_motion", "result.json"),
    "depth_build": evidence / "d2_depth_build_receipt.json",
    "kiss_icp_build": evidence / "d2_kiss_icp_build_receipt.json",
    "nav2_packages": evidence / "d2_nav2_package_receipt.json",
    "fastlio_packages": evidence / "d2_fastlio_package_receipt.json",
    "fastlio_build": evidence / "d2_fastlio_build_receipt.json",
}

components = {}
for name, path in sources.items():
    data = json.loads(path.read_text(encoding="utf-8"))
    status = data.get("status", data.get("result"))
    components[name] = {
        "status": status,
        "evidence": str(path.relative_to(root)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }

cpu_summary = json.loads(sources["official_onnx_cpu"].read_text(encoding="utf-8"))
safety = subprocess.run(
    ["bash", str(root / "scripts/verify_s100_safety.sh")],
    check=False,
    capture_output=True,
    text=True,
)
all_pass = all(item["status"] == "PASS" for item in components.values())
payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "phase": "D2",
    "scope": "S100 CPU reproduction of X5 official O0-O6 plus U1-U3; offline only",
    "components": components,
    "performance": {
        "policy_p99_ms_max_observed": cpu_summary.get("policy_p99_ms_max"),
        "policy_p99_target_ms": cpu_summary.get("policy_p99_target_ms"),
        "policy_p99_target_observed": cpu_summary.get("policy_p99_target_observed"),
        "idle_acceptance_gate": cpu_summary.get("benchmark_idle_gate"),
        "note": "D2 proves CPU functional reproduction; busy-desktop timing is observed, not final idle acceptance.",
    },
    "safety": {
        "exit_code": safety.returncode,
        "output": safety.stdout.strip(),
        "device_access": False,
        "robot_control_output": False,
        "ros_domain_id": 42,
    },
    "required_components_pass": all_pass,
    "result": "PASS" if all_pass and safety.returncode == 0 else "FAIL",
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
