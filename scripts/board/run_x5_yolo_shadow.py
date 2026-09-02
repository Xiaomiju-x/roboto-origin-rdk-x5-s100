#!/usr/bin/env python3
"""Run the verified X5 detector/tracker baseline and emit file-only shadow evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path

from roboto_upgrade.yolo_shadow import make_shadow_decision, normalize_pixel_detections

EXPECTED = {
    "model": "239ce8fedb03fc7cc1700ce0b19e25131e35ee53a18aa4caa678d7cd0ca92134",
    "fixture": "fc5c286b54dd679966883c466926e51688fd4e20fdfbb0244e83bb402dc0545f",
    "binary": "2d49c3bc1c5feb8211fab1ced64bf16298c13d91af8894bd0162d792f3238bda",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_tracks(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise RuntimeError("tracker produced no rows")
    result = []
    for row in rows:
        parsed = {
            "frame_idx": int(row["frame_idx"]),
            "track_id": int(row["track_id"]),
            "class_id": int(row["class_id"]),
            "score": float(row["score"]),
            "x1": float(row["x1"]),
            "y1": float(row["y1"]),
            "x2": float(row["x2"]),
            "y2": float(row["y2"]),
        }
        if not (parsed["x2"] > parsed["x1"] and parsed["y2"] > parsed["y1"]):
            raise RuntimeError("tracker emitted a zero-area box")
        result.append(parsed)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if os.environ.get("ROS_DOMAIN_ID") != "42":
        raise RuntimeError("ROS_DOMAIN_ID must be 42")
    if platform.machine() != "aarch64" or "RDK X5" not in Path(
        "/sys/firmware/devicetree/base/model"
    ).read_bytes().rstrip(b"\0").decode():
        raise RuntimeError("board identity mismatch")
    for label, path in (("binary", args.binary), ("model", args.model), ("fixture", args.fixture)):
        if sha256(path) != EXPECTED[label]:
            raise RuntimeError(f"{label} hash mismatch")
    if subprocess.run(["pgrep", "-x", "mot_tros_demo"], capture_output=True, check=False).returncode == 0:
        raise RuntimeError("pre-existing mot_tros_demo process")

    args.work_dir.mkdir(parents=True, exist_ok=True)
    hashes = []
    elapsed = []
    final_tracks = None
    for index in range(args.runs):
        track_path = args.work_dir / f"tracks_{index + 1:02d}.csv"
        log_path = args.work_dir / f"run_{index + 1:02d}.log"
        command = [
            str(args.binary),
            "--model",
            str(args.model),
            "--video",
            str(args.fixture),
            "--tracker",
            "bytetrack",
            "--track-thresh",
            "0.5",
            "--match-thresh",
            "0.3",
            "--min-conf",
            "0.1",
            "--track-buffer",
            "30",
            "--det-freq",
            "1",
            "--classes-num",
            "6",
            "--reg",
            "1",
            "--score-thres",
            "0.35",
            "--nms-thres",
            "0.7",
            "--resize-type",
            "1",
            "--duration",
            "0",
            "--no-render",
            "--track-log",
            str(track_path),
        ]
        started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        elapsed.append((time.perf_counter() - started) * 1000.0)
        log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(f"detector/tracker run {index + 1} failed")
        tracks = load_tracks(track_path)
        hashes.append(sha256(track_path))
        final_tracks = tracks
    if len(set(hashes)) != 1 or final_tracks is None:
        raise RuntimeError("X5 detector/tracker output is not deterministic")
    first_frame = [row for row in final_tracks if row["frame_idx"] == 0]
    normalized = normalize_pixel_detections(first_frame, image_width=810, image_height=1080)
    decision = make_shadow_decision(normalized, platform="x5", frame_id=0)
    if subprocess.run(["pgrep", "-x", "mot_tros_demo"], capture_output=True, check=False).returncode == 0:
        raise RuntimeError("mot_tros_demo process residue")
    payload = {
        "schema": "roboto_origin.x5_yolo_shadow.v1",
        "status": "X5_YOLO26_BYTETRACK_SHADOW_FILE_PASS",
        "platform": "x5",
        "role": "native_robot_baseline_before_s_series_migration",
        "execution_unit": "BPU_DETECT_CPU_TRACK",
        "model": {"family": "YOLO26s", "sha256": EXPECTED["model"], "redistributed": False},
        "fixture": {"sha256": EXPECTED["fixture"], "redistributed": False},
        "runs": args.runs,
        "track_rows": len(final_tracks),
        "track_ids": len({row["track_id"] for row in final_tracks}),
        "csv_sha256": hashes[0],
        "deterministic": True,
        "process_latency_ms": {
            "p50": statistics.median(elapsed),
            "min": min(elapsed),
            "max": max(elapsed),
        },
        "shadow_decision": decision,
        "claims": {
            "real_bpu_inference": True,
            "file_input_file_sink": True,
            "camera_used": False,
            "robot_or_peripheral_output": False,
            "motion_command_emitted": False,
            "physical_robot_validated": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": payload["status"], "track_rows": len(final_tracks)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
