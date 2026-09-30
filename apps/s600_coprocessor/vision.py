"""Bounded S600 BPU application/benchmark using unchanged D-Robotics wrappers."""

import argparse
import hashlib
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np


def stats(values):
    return (
        {
            "count": len(values),
            "p50_ms": float(np.percentile(values, 50)),
            "p95_ms": float(np.percentile(values, 95)),
            "max_ms": float(max(values)),
        }
        if values
        else {}
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", type=Path, required=True)
    ap.add_argument("--task", choices=["seg", "pose", "detect"], default="seg")
    ap.add_argument("--model", required=True)
    ap.add_argument("--image")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--live", action="store_true")
    ap.add_argument(
        "--rgbd",
        action="store_true",
        help="official OpenNI depth-to-color registration",
    )
    ap.add_argument(
        "--openni-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "vendor_openni",
    )
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--cores", type=int, nargs="+")
    ap.add_argument("--priority", type=int, default=0)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if not 1 <= args.seconds <= 600:
        raise ValueError("seconds must be 1..600")
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [
        str(args.upstream.resolve()),
        str(args.upstream.resolve() / "samples/vision/ultralytics_yolo/runtime/python"),
    ]
    from yolo_detect import YoloDetect, YoloDetectConfig
    from yolo_pose import YoloPose, YoloPoseConfig
    from yolo_seg import YoloSeg, YoloSegConfig

    classes = {
        "seg": (YoloSeg, YoloSegConfig),
        "pose": (YoloPose, YoloPoseConfig),
        "detect": (YoloDetect, YoloDetectConfig),
    }
    cls, cfg = classes[args.task]
    model = cls(cfg(model_path=args.model))
    model.set_scheduling_params(priority=args.priority, bpu_cores=args.cores)
    labels_path = args.upstream / "datasets/coco/coco_classes.names"
    labels = labels_path.read_text().splitlines() if labels_path.exists() else []
    cap = None
    bridge = None
    bridge_log = None
    depth_image = None
    rgbd_meta = None
    intrinsics = None
    stop_capture = threading.Event()
    capture_threads = []
    color_queue = queue.Queue(maxsize=1)
    latest_depth = {}
    capture_errors = []
    capture_counts = {"color_frames": 0, "depth_frames": 0, "color_queue_replaced": 0}
    status = {
        "status": "INCOMPLETE",
        "backend": "hbm_runtime/BPU",
        "task": args.task,
        "model": Path(args.model).name,
        "model_sha256": hashlib.sha256(Path(args.model).read_bytes()).hexdigest(),
        "cores": args.cores,
        "priority": args.priority,
        "source": "live_openni_registered_rgbd"
        if args.rgbd
        else "live_usb_camera"
        if args.live
        else "fixed_image_replay",
        "motion_output": False,
        "distance_association": "UNREGISTERED_RGB_DEPTH",
    }
    timings = {k: [] for k in ["capture", "pre", "bpu_call", "post", "e2e"]}
    frames = 0
    detections = {}
    ranged_objects = 0
    ages = []

    def pump_depth():
        try:
            while not stop_capture.is_set():
                header = bridge.stdout.read(40)
                if len(header) != 40:
                    raise RuntimeError("RGBD stream ended; inspect rgbd.stderr")
                magic, w, h, td, tc, mono, reg = struct.unpack("<4sIIQQQI", header)
                if magic != b"RGBD" or w != 640 or h != 480 or reg not in [0, 1]:
                    raise RuntimeError("RGBD registration/shape contract failed")
                depth = bridge.stdout.read(w * h * 2)
                if len(depth) != w * h * 2:
                    raise RuntimeError("RGBD frame truncated")
                latest_depth["frame"] = (
                    np.frombuffer(depth, "<u2").reshape(h, w),
                    td,
                    mono,
                    reg,
                )
                capture_counts["depth_frames"] += 1
        except Exception as exc:
            if not stop_capture.is_set():
                capture_errors.append(str(exc))

    def pump_color():
        try:
            while not stop_capture.is_set():
                ok, image = cap.read()
                ns = time.monotonic_ns()
                if not ok or image.shape[:2] != (480, 640):
                    raise RuntimeError("UVC color frame/shape failed")
                capture_counts["color_frames"] += 1
                if color_queue.full():
                    try:
                        color_queue.get_nowait()
                        capture_counts["color_queue_replaced"] += 1
                    except queue.Empty:
                        pass
                color_queue.put_nowait((ns, image))
        except Exception as exc:
            if not stop_capture.is_set():
                capture_errors.append(str(exc))

    def read_rgbd():
        if capture_errors:
            raise RuntimeError(capture_errors[0])
        color_host_ns, image = color_queue.get(timeout=5)
        deadline = time.monotonic() + 5
        while "frame" not in latest_depth:
            if capture_errors:
                raise RuntimeError(capture_errors[0])
            if time.monotonic() > deadline:
                raise TimeoutError("depth first frame")
            time.sleep(0.005)
        depth, td, mono, reg = latest_depth["frame"]
        return (
            image,
            depth,
            {
                "depth_timestamp_us": td,
                "color_timestamp_us": None,
                "host_monotonic_ns": mono,
                "color_host_monotonic_ns": color_host_ns,
                "pair_delta_ms": abs(color_host_ns - mono) / 1e6,
                "sdk_registration": reg,
                "time_alignment": "latest_host_software_pair_not_hardware_sync",
            },
        )

    try:
        if args.rgbd:
            cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                raise RuntimeError("Astra UVC camera failed")
            bridge_log = (args.output / "rgbd.stderr").open("x")
            bridge = subprocess.Popen(
                [
                    "sudo",
                    "-n",
                    "env",
                    f"LD_LIBRARY_PATH={args.openni_dir.resolve()}",
                    "timeout",
                    str(args.seconds + 20),
                    str(Path(__file__).resolve().parent / "rgbd_bridge"),
                    str(args.seconds + 5),
                ],
                stdout=subprocess.PIPE,
                stderr=bridge_log,
            )
            capture_threads = [
                threading.Thread(target=pump_depth),
                threading.Thread(target=pump_color),
            ]
            for worker in capture_threads:
                worker.start()
            image, depth_image, rgbd_meta = read_rgbd()
            calibration = json.loads(
                (args.output / "rgbd.stderr").read_text().splitlines()[0]
            )
            if calibration.get("valid") and all(
                np.isfinite(calibration["rgb_intrinsics_640"])
            ):
                fx, fy, cx, cy = calibration["rgb_intrinsics_640"]
                if fx > 0 and fy > 0:
                    intrinsics = (fx, fy, cx, cy)
            status["factory_calibration"] = calibration
            status["distance_association"] = (
                "OPENNI_FACTORY_REGISTRATION_UVC_SOFTWARE_PAIR"
                if intrinsics
                else "FACTORY_RGB_CALIBRATION_INVALID_DISTANCE_DISABLED"
            )
            if not intrinsics:
                status["source"] = "live_uvc_plus_unregistered_depth"
        elif args.live:
            cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                raise RuntimeError("camera open failed")
            ok, image = cap.read()
            if not ok:
                raise RuntimeError("camera frame failed")
        else:
            image = cv2.imread(args.image)
            if image is None:
                raise RuntimeError("image not readable")
        status["input_shape"] = list(image.shape)
        # Warmup excluded from recorded measurements.
        for _ in range(5):
            model.predict(image)
        begin = time.monotonic()
        last_save = -1
        with (args.output / "events.jsonl").open("x") as events:
            while time.monotonic() - begin < args.seconds:
                t0 = time.perf_counter()
                if args.rgbd:
                    image, depth_image, rgbd_meta = read_rgbd()
                elif args.live:
                    ok, image = cap.read()
                    if not ok:
                        raise RuntimeError("camera stream ended")
                t1 = time.perf_counter()
                inp = model.pre_process(image)
                t2 = time.perf_counter()
                outputs = model.forward(inp)
                t3 = time.perf_counter()
                result = model.post_process(outputs, image.shape[1], image.shape[0])
                t4 = time.perf_counter()
                for key, value in zip(
                    timings, [t1 - t0, t2 - t1, t3 - t2, t4 - t3, t4 - t0]
                ):
                    timings[key].append(value * 1000)
                boxes, scores, ids = result[:3]
                objects = []
                for i, (box, score, cid) in enumerate(zip(boxes, scores, ids)):
                    cid = int(cid)
                    label = labels[cid] if cid < len(labels) else str(cid)
                    detections[label] = detections.get(label, 0) + 1
                    obj = {
                        "class_id": cid,
                        "label": label,
                        "confidence": float(score),
                        "box_xyxy": box.tolist(),
                        "distance_m": None,
                    }
                    if args.task == "seg":
                        obj["mask_pixels"] = int(np.count_nonzero(result[3][i]))
                        if (
                            depth_image is not None
                            and intrinsics
                            and rgbd_meta["sdk_registration"] == 1
                        ):
                            x1, y1, x2, y2 = np.asarray(box, dtype=int)
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
                            age_ms = (
                                time.monotonic_ns() - rgbd_meta["host_monotonic_ns"]
                            ) / 1e6
                            if x2 > x1 and y2 > y1:
                                mask = (
                                    cv2.resize(
                                        result[3][i].astype(np.uint8),
                                        (x2 - x1, y2 - y1),
                                    )
                                    > 0
                                )
                                values = depth_image[y1:y2, x1:x2][mask]
                                valid = values[(values >= 600) & (values <= 8000)]
                                obj["depth_valid_pixels"] = len(valid)
                                obj["depth_valid_fraction"] = len(valid) / max(
                                    1, len(values)
                                )
                                if (
                                    len(valid) >= 30
                                    and obj["depth_valid_fraction"] >= 0.2
                                    and rgbd_meta["pair_delta_ms"] <= 100
                                    and age_ms <= 250
                                ):
                                    obj["distance_m"] = float(np.median(valid)) / 1000
                                    obj["distance_kind"] = (
                                        "camera_optical_axis_depth_not_euclidean_range"
                                    )
                                    if intrinsics:
                                        fx, fy, cx, cy = intrinsics
                                        yy, xx = np.where(
                                            mask
                                            & (depth_image[y1:y2, x1:x2] >= 600)
                                            & (depth_image[y1:y2, x1:x2] <= 8000)
                                        )
                                        zz = (
                                            depth_image[y1:y2, x1:x2][yy, xx].astype(
                                                float
                                            )
                                            / 1000
                                        )
                                        obj["position_rgb_optical_m"] = [
                                            float(np.median((xx + x1 - cx) * zz / fx)),
                                            float(np.median((yy + y1 - cy) * zz / fy)),
                                            float(np.median(zz)),
                                        ]
                                        obj["position_frame"] = (
                                            "factory_rgb_optical_not_robot_base"
                                        )
                                    ranged_objects += 1
                    objects.append(obj)
                event = {
                    "frame": frames,
                    "host_monotonic_ns": time.monotonic_ns(),
                    "objects": objects,
                    "e2e_ms": (t4 - t0) * 1000,
                    "bpu_call_ms": (t3 - t2) * 1000,
                }
                if rgbd_meta:
                    event["rgbd"] = rgbd_meta
                    ages.append(
                        (time.monotonic_ns() - rgbd_meta["host_monotonic_ns"]) / 1e6
                    )
                    valid_depth = depth_image[
                        (depth_image >= 600) & (depth_image <= 8000)
                    ]
                    event["native_depth_median_m"] = (
                        float(np.median(valid_depth)) / 1000
                        if len(valid_depth)
                        else None
                    )
                    event["native_depth_valid_pixels"] = len(valid_depth)
                events.write(json.dumps(event) + "\n")
                latest_temp = args.output / "latest_event.tmp.json"
                latest_temp.write_text(json.dumps(event))
                os.replace(latest_temp, args.output / "latest_event.json")
                second = int(time.monotonic() - begin)
                if second != last_save:
                    canvas = image.copy()
                    for i, obj in enumerate(objects):
                        x1, y1, x2, y2 = np.asarray(obj["box_xyxy"], dtype=int)
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
                        if args.task == "seg" and x2 > x1 and y2 > y1:
                            mask = (
                                cv2.resize(
                                    result[3][i].astype(np.uint8), (x2 - x1, y2 - y1)
                                )
                                > 0
                            )
                            roi = canvas[y1:y2, x1:x2]
                            roi[mask] = (
                                roi[mask] * 0.5 + np.array([40, 210, 80]) * 0.5
                            ).astype(np.uint8)
                        cv2.rectangle(canvas, (x1, y1), (x2, y2), (40, 210, 80), 2)
                        label = obj["label"] + (
                            f" {obj['distance_m']:.2f}m"
                            if obj["distance_m"] is not None
                            else ""
                        )
                        cv2.putText(
                            canvas,
                            label,
                            (x1, max(15, y1)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 0, 255),
                            1,
                        )
                    cv2.imwrite(str(args.output / "latest.jpg"), canvas)
                    raw_temp = args.output / "latest_input.tmp.jpg"
                    cv2.imwrite(str(raw_temp), image)
                    os.replace(raw_temp, args.output / "latest_input.jpg")
                    if frames == 0:
                        cv2.imwrite(str(args.output / "input.jpg"), image)
                    last_save = second
                frames += 1
        status.update(
            status="PASS",
            elapsed_s=time.monotonic() - begin,
            frames=frames,
            throughput_fps=frames / (time.monotonic() - begin),
            timing={k: stats(v) for k, v in timings.items()},
            object_counts=detections,
        )
        status.update(
            ranged_object_observations=ranged_objects,
            frame_age=stats(ages),
            robot_frame_position_validated=False,
            capture_counts=capture_counts,
        )
    except Exception as exc:
        status.update(status="FAIL", error=str(exc), frames=frames)
        raise
    finally:
        stop_capture.set()
        for worker in capture_threads:
            worker.join(timeout=3)
        status["capture_threads_finished"] = all(
            not worker.is_alive() for worker in capture_threads
        )
        if cap is not None:
            cap.release()
        if bridge is not None:
            bridge.stdout.close()
            try:
                bridge.wait(timeout=3)
            except subprocess.TimeoutExpired:
                bridge.terminate()
                try:
                    bridge.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    bridge.kill()
                    bridge.wait()
            status["bridge_exit"] = bridge.returncode
        if bridge_log is not None:
            bridge_log.close()
        (args.output / "summary.json").write_text(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
