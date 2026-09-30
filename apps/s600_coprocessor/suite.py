"""Single and concurrent BPU replay with isolated processes and board telemetry."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def telemetry():
    readings = {}
    for pattern in [
        "/sys/devices/platform/soc/*.bpu/ratio",
        "/sys/devices/system/bpu/ratio",
        "/sys/class/thermal/thermal_zone*/temp",
    ]:
        import glob

        for name in glob.glob(pattern):
            try:
                readings[name] = Path(name).read_text().strip()
            except OSError:
                pass
    readings["loadavg"] = Path("/proc/loadavg").read_text().strip()
    readings["meminfo"] = {
        line.split(":")[0]: line.split(":")[1].strip()
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemAvailable:", "MemFree:"))
    }
    return readings


def run_case(root, case, jobs, seconds, image, sensors):
    output = root / case
    output.mkdir()
    processes, logs = [], []
    try:
        for i, (task, model, cores) in enumerate(jobs):
            cmd = [
                "/usr/bin/python3",
                "vision.py",
                "--upstream",
                "upstream",
                "--task",
                task,
                "--model",
                model,
                "--seconds",
                str(seconds),
                "--image",
                image,
                "--output",
                str(output / f"job{i}"),
            ]
            if cores is not None:
                cmd += ["--cores"] + list(map(str, cores))
            log = (output / f"job{i}.log").open("x")
            logs.append(log)
            processes.append(
                subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            )
        if sensors:
            root = Path(__file__).resolve().parent
            log = (output / "sensors.log").open("x")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    [
                        "/usr/bin/python3",
                        str(root / "sensor_bundle.py"),
                        "--seconds",
                        str(seconds),
                        "--output",
                        str((output / "sensors").resolve()),
                    ],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        samples = []
        deadline = time.monotonic() + seconds + 75
        while any(p.poll() is None for p in processes):
            samples.append({"host_monotonic_ns": time.monotonic_ns(), **telemetry()})
            if time.monotonic() > deadline:
                raise TimeoutError("case exceeded bounded deadline")
            time.sleep(0.5)
        (output / "case.json").write_text(
            json.dumps(
                {
                    "exits": [p.returncode for p in processes],
                    "telemetry": samples,
                    "sensors_enabled": sensors,
                },
                indent=2,
            )
        )
        if any(p.returncode != 0 for p in processes):
            raise RuntimeError("case failed; inspect logs")
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        for log in logs:
            log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--seg-model", required=True)
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args()
    if not 12 <= args.seconds <= 60:
        raise ValueError("full suite seconds must be 12..60 for reused sensor wrapper")
    os.environ.update(
        ROS_DOMAIN_ID="42",
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    args.output.mkdir(parents=True, exist_ok=False)
    image = "upstream/samples/vision/ultralytics_yolo/test_data/bus.jpg"
    pose = "/opt/hobot/model/s600/basic/yolo11n_pose_nashp_640x640_nv12.hbm"
    for case, jobs, sensors in [
        ("seg_alone", [("seg", args.seg_model, None)], False),
        ("pose_alone", [("pose", pose, None)], False),
        (
            "parallel_default",
            [("seg", args.seg_model, None), ("pose", pose, None)],
            False,
        ),
        (
            "parallel_samecore",
            [("seg", args.seg_model, [0]), ("pose", pose, [0])],
            False,
        ),
        ("parallel_split", [("seg", args.seg_model, [0]), ("pose", pose, [1])], False),
        (
            "parallel_split_sensors",
            [("seg", args.seg_model, [0]), ("pose", pose, [1])],
            True,
        ),
        ("parallel_fourcore", [("seg", args.seg_model, [i]) for i in range(4)], False),
    ]:
        print("CASE", case, flush=True)
        run_case(args.output, case, jobs, args.seconds, image, sensors)


if __name__ == "__main__":
    main()
