"""Live RGBD BPU segmentation + second BPU task + microphone/lidar + online planning."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from suite import telemetry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    if not 12 <= args.seconds <= 120:
        raise ValueError("12..120 seconds")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    base = Path(__file__).resolve().parent
    os.environ.update(
        ROS_DOMAIN_ID="42",
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    commands = {
        "vision": [
            "/usr/bin/python3",
            "vision.py",
            "--upstream",
            "upstream",
            "--task",
            "seg",
            "--model",
            args.model,
            "--rgbd",
            "--seconds",
            str(args.seconds),
            "--cores",
            "0",
            "--output",
            str(args.output / "vision"),
        ],
        "pose": [
            "/usr/bin/python3",
            "vision.py",
            "--upstream",
            "upstream",
            "--task",
            "pose",
            "--model",
            "/opt/hobot/model/s600/basic/yolo11n_pose_nashp_640x640_nv12.hbm",
            "--seconds",
            str(args.seconds),
            "--cores",
            "1",
            "--image",
            "upstream/samples/vision/ultralytics_yolo/test_data/bus.jpg",
            "--output",
            str(args.output / "pose"),
        ],
        "sensors": [
            str(base / ".venv/bin/python"),
            "sensor.py",
            "--serial",
            "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
            "--model",
            str(base / "models/vosk-model-small-cn-0.22"),
            "--seconds",
            str(args.seconds),
            "--output",
            str(args.output / "sensors"),
        ],
        "planner": [
            "/usr/bin/python3",
            "scene.py",
            "--events",
            str(args.output / "sensors/events.jsonl"),
            "--vision-events",
            str(args.output / "vision/events.jsonl"),
            "--output",
            str(args.output / "planner"),
            "--seconds",
            str(args.seconds + 3),
        ],
    }
    logs = {}
    procs = {}
    samples = []
    try:
        for name, cmd in commands.items():
            logs[name] = (args.output / f"{name}.log").open("x")
            procs[name] = subprocess.Popen(
                cmd, stdout=logs[name], stderr=subprocess.STDOUT, start_new_session=True
            )
        deadline = time.monotonic() + args.seconds + 45
        while any(p.poll() is None for p in procs.values()):
            samples.append({"host_monotonic_ns": time.monotonic_ns(), **telemetry()})
            if time.monotonic() > deadline:
                raise TimeoutError("joint deadline")
            time.sleep(0.5)
    finally:
        import signal

        for proc in procs.values():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        for log in logs.values():
            log.close()
        result = {
            "exits": {name: p.returncode for name, p in procs.items()},
            "telemetry": samples,
            "pose_source": "fixed_image_replay_secondary_load",
            "motion_output": False,
        }
        (args.output / "joint.json").write_text(json.dumps(result, indent=2))
    if any(p.returncode != 0 for p in procs.values()):
        raise RuntimeError("joint failed; inspect logs")


if __name__ == "__main__":
    main()
