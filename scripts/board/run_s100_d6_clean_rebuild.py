#!/usr/bin/env python3
"""Rebuild locked S100 bundles into a new path and replay D3/D4 fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_lock(root: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    files = 0
    bytes_total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        file_hash = sha256(path)
        size = path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        files += 1
        bytes_total += size
    return {"files": files, "bytes": bytes_total, "tree_sha256": digest.hexdigest()}


def run_logged(command: list[str], log_path: Path, env: dict[str, str]) -> int:
    with log_path.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            command,
            cwd=log_path.parent,
            env=env,
            text=True,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    destination = args.destination.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "S100 clean-path reconstruction and representative offline replay",
        "source_root": str(root),
        "destination": str(destination),
        "external_network": False,
        "external_device_access": False,
        "control_output": False,
        "result": "FAIL",
    }

    try:
        if destination.exists():
            raise FileExistsError(f"clean destination already exists: {destination}")
        source_bpu = root / "bpu_bundle"
        source_d4 = root / "d4_official_bundle"
        for required in (
            source_bpu / "manifest.json",
            source_bpu / "logs/conversion_summary.json",
            source_d4 / "manifest.json",
        ):
            if not required.is_file():
                raise FileNotFoundError(required)

        source_locks = {"bpu_bundle": tree_lock(source_bpu), "d4_official_bundle": tree_lock(source_d4)}
        source_bytes = sum(int(item["bytes"]) for item in source_locks.values())
        free_before = shutil.disk_usage(destination.parent).free
        if free_before < source_bytes * 2 + 512 * 1024 * 1024:
            raise RuntimeError(
                f"insufficient free space: free={free_before} source={source_bytes}"
            )

        destination.mkdir(parents=True, exist_ok=False)
        shutil.copytree(source_bpu, destination / "bpu_bundle", symlinks=True)
        shutil.copytree(source_d4, destination / "d4_official_bundle", symlinks=True)
        copied_locks = {
            "bpu_bundle": tree_lock(destination / "bpu_bundle"),
            "d4_official_bundle": tree_lock(destination / "d4_official_bundle"),
        }
        copied_exactly = source_locks == copied_locks

        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        d3_result_path = args.output.parent / "d3_replay.json"
        d4_result_path = args.output.parent / "d4_replay.json"
        d3_returncode = run_logged(
            [
                "python3",
                str(root / "scripts/run_s100_bpu_ab.py"),
                "--bundle",
                str(destination / "bpu_bundle"),
                "--output",
                str(d3_result_path),
                "--warmups",
                "20",
                "--runs",
                "100",
            ],
            args.output.parent / "d3_replay.log",
            env,
        )
        d4_returncode = run_logged(
            [
                "python3",
                str(root / "scripts/run_s100_d4_official_smoke.py"),
                "--bundle",
                str(destination / "d4_official_bundle"),
                "--output",
                str(d4_result_path),
                "--warmups",
                "20",
                "--runs",
                "100",
                "--bytetrack-max-frames",
                "120",
            ],
            args.output.parent / "d4_replay.log",
            env,
        )
        d3 = json.loads(d3_result_path.read_text(encoding="utf-8")) if d3_result_path.exists() else {}
        d4 = json.loads(d4_result_path.read_text(encoding="utf-8")) if d4_result_path.exists() else {}
        post_replay_locks = {
            "bpu_bundle": tree_lock(destination / "bpu_bundle"),
            "d4_official_bundle": tree_lock(destination / "d4_official_bundle"),
        }
        checks = {
            "new_destination_created": destination.is_dir(),
            "source_and_copy_tree_hashes_equal": copied_exactly,
            "replay_did_not_mutate_rebuilt_bundles": copied_locks == post_replay_locks,
            "d3_replay_exit_zero": d3_returncode == 0,
            "d3_replay_pass": d3.get("result") == "PASS",
            "d3_replay_no_failures": d3.get("summary", {}).get("fail") == 0,
            "d4_replay_exit_zero": d4_returncode == 0,
            "d4_replay_pass_4_of_4": d4.get("result") == "PASS"
            and d4.get("summary", {}).get("pass") == 4
            and d4.get("summary", {}).get("fail") == 0,
            "d4_replay_no_device_or_control": d4.get("external_device_access") is False
            and d4.get("control_output") is False,
        }
        payload.update(
            {
                "disk": {
                    "free_bytes_before": free_before,
                    "free_bytes_after": shutil.disk_usage(destination.parent).free,
                    "source_bytes": source_bytes,
                },
                "source_locks": source_locks,
                "copied_locks": copied_locks,
                "post_replay_locks": post_replay_locks,
                "replays": {
                    "d3": {
                        "returncode": d3_returncode,
                        "result_file": str(d3_result_path),
                        "result_sha256": sha256(d3_result_path) if d3_result_path.exists() else None,
                        "summary": d3.get("summary"),
                        "result": d3.get("result"),
                    },
                    "d4": {
                        "returncode": d4_returncode,
                        "result_file": str(d4_result_path),
                        "result_sha256": sha256(d4_result_path) if d4_result_path.exists() else None,
                        "summary": d4.get("summary"),
                        "result": d4.get("result"),
                    },
                },
                "checks": checks,
                "result": "PASS" if all(checks.values()) else "FAIL",
            }
        )
    except Exception as error:  # retain exact partial rebuild for diagnosis
        payload["error"] = f"{type(error).__name__}: {error}"

    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": payload["result"],
                "destination": str(destination),
                "output": str(args.output),
                "error": payload.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
