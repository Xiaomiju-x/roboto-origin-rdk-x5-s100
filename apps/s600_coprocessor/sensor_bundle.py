"""Standalone depth/lidar/microphone sidecar used by the performance suite."""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args()
    if not 12 <= args.seconds <= 60:
        raise ValueError("seconds 12..60")
    root = Path(__file__).resolve().parent
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["ROS_DOMAIN_ID"] = "42"
    logs = []
    children = []
    try:
        for name, cmd in [
            (
                "depth",
                [
                    "sudo",
                    "-n",
                    "env",
                    f"LD_LIBRARY_PATH={root}/vendor_openni",
                    "timeout",
                    str(args.seconds + 5),
                    str(root / "depth_cloud"),
                    str(args.seconds),
                ],
            ),
            (
                "lidar_mic",
                [
                    str(root / ".venv/bin/python"),
                    str(root / "sensor.py"),
                    "--serial",
                    "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
                    "--model",
                    str(root / "models/vosk-model-small-cn-0.22"),
                    "--seconds",
                    str(args.seconds),
                    "--output",
                    str(out / "lidar_mic"),
                ],
            ),
        ]:
            log = (out / f"{name}.log").open("x")
            logs.append(log)
            children.append(
                subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=root,
                    start_new_session=True,
                )
            )
        deadline = time.monotonic() + args.seconds + 20
        while any(p.poll() is None for p in children):
            if time.monotonic() > deadline:
                raise TimeoutError("sensor deadline")
            time.sleep(0.2)
    finally:
        for p in children:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
        for f in logs:
            f.close()
        (out / "summary.json").write_text(
            json.dumps(
                {"exits": [p.returncode for p in children], "motion_output": False},
                indent=2,
            )
        )
    if any(p.returncode for p in children):
        raise RuntimeError("sensor sidecar failed")


if __name__ == "__main__":
    main()
