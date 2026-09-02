#!/usr/bin/env python3
"""Produce an evidence-backed D4 frontier decision without installing or running devices."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


REQUIRED_TROS_PACKAGES = (
    "tros-humble-hobot-centerpoint",
    "tros-humble-hobot-stereonet",
    "tros-humble-dnn-node",
    "tros-humble-hobot-cv",
    "tros-humble-ai-msgs",
)


def package_version(name: str) -> str | None:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--official-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    decision = json.loads(args.decision.read_text(encoding="utf-8"))
    official = json.loads(args.official_result.read_text(encoding="utf-8"))
    tools = {name: shutil.which(name) for name in ("colcon", "cmake", "g++", "git-lfs")}
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in ("hbm_runtime", "torch", "onnxruntime", "cv2", "numpy", "scipy")
    }
    packages = {name: package_version(name) for name in REQUIRED_TROS_PACKAGES}
    filesystem = {
        "/opt/ros/humble/setup.bash": Path("/opt/ros/humble/setup.bash").exists(),
        "/opt/tros/humble/setup.bash": Path("/opt/tros/humble/setup.bash").exists(),
    }

    official_ok = (
        official.get("result") == "PASS"
        and official.get("summary", {}).get("total") == 4
        and official.get("summary", {}).get("pass") == 4
        and official.get("summary", {}).get("fail") == 0
        and official.get("external_network") is False
        and official.get("external_device_access") is False
        and official.get("control_output") is False
    )
    decisions = {item["id"]: item["decision"] for item in decision["repositories"]}
    expected = {
        "F1": "PASS_DEPLOYED",
        "F2": "BLOCKED_SYSTEM_DEPENDENCY",
        "F3": "BLOCKED_SYSTEM_DEPENDENCY",
        "F6": "REFERENCE_ONLY_DATASET_REQUIRED",
    }
    decision_contract_ok = all(decisions.get(key) == value for key, value in expected.items())
    dependency_evidence_ok = (
        tools["colcon"] is None
        and tools["git-lfs"] is None
        and not filesystem["/opt/tros/humble/setup.bash"]
        and all(value is None for value in packages.values())
        and modules["hbm_runtime"]
        and not modules["torch"]
    )

    checks = {
        "official_smoke_4_of_4": official_ok,
        "frontier_decisions_match_locked_contract": decision_contract_ok,
        "blocked_dependency_claims_reproduced": dependency_evidence_ok,
        "all_candidates_have_immutable_commit": all(
            len(item.get("commit", "")) == 40 for item in decision["repositories"]
        ),
        "no_candidate_is_misreported_as_deployed": all(
            item["decision"] == "PASS_DEPLOYED" or "DEPLOY" not in item["decision"]
            for item in decision["repositories"]
        ),
    }
    result = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": decision["scope"],
        "official_result": str(args.official_result),
        "official_summary": official["summary"],
        "board_dependency_snapshot": {
            "tools": tools,
            "python_modules": modules,
            "debian_packages": packages,
            "filesystem": filesystem,
        },
        "candidate_decisions": decision["repositories"],
        "checks": checks,
        "external_network": False,
        "external_device_access": False,
        "control_output": False,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    print(json.dumps({"result": result["result"], "checks": checks}, ensure_ascii=False))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
