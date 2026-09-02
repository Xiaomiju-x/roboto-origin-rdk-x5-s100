#!/usr/bin/env python3
"""Run D-Robotics S100 Model Zoo samples from frozen files only."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import traceback
import types
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def benchmark(callable_, warmups: int, runs: int) -> dict[str, float | int]:
    for _ in range(warmups):
        callable_()
    samples = []
    for _ in range(runs):
        started = time.perf_counter_ns()
        callable_()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    values = np.asarray(samples, dtype=np.float64)
    return {
        "warmups": warmups,
        "runs": runs,
        "p50_ms": float(np.percentile(values, 50, method="linear")),
        "p95_ms": float(np.percentile(values, 95, method="linear")),
        "p99_ms": float(np.percentile(values, 99, method="linear")),
        "max_ms": float(np.max(values)),
        "mean_ms": statistics.fmean(samples),
    }


def only_output(result: dict[str, dict[str, np.ndarray]]) -> np.ndarray:
    if len(result) != 1:
        raise RuntimeError(f"expected one model result, got {list(result)}")
    model_outputs = next(iter(result.values()))
    if len(model_outputs) != 1:
        raise RuntimeError(f"expected one output, got {list(model_outputs)}")
    return np.asarray(next(iter(model_outputs.values())))


def verify_bundle(bundle: Path) -> tuple[dict[str, object], str]:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["assets"]:
        path = bundle / item["path"]
        if not path.is_file():
            raise RuntimeError(f"bundle asset missing: {item['path']}")
        if path.stat().st_size != item["bytes"]:
            raise RuntimeError(f"bundle size mismatch: {item['path']}")
        if sha256(path) != item["sha256"]:
            raise RuntimeError(f"bundle hash mismatch: {item['path']}")
    return manifest, sha256(manifest_path)


def install_bytetrack_compatibility_modules() -> dict[str, str]:
    """Supply auditable NumPy/SciPy equivalents for two optional C extensions."""

    lap_module = types.ModuleType("lap")

    def lapjv(cost_matrix, extend_cost=True, cost_limit=np.inf):
        del extend_cost
        cost = np.asarray(cost_matrix, dtype=np.float64)
        safe = np.where(np.isfinite(cost), cost, 1e12)
        rows, cols = linear_sum_assignment(safe)
        x = np.full(cost.shape[0], -1, dtype=np.int64)
        y = np.full(cost.shape[1], -1, dtype=np.int64)
        total = 0.0
        for row, col in zip(rows, cols):
            value = cost[row, col]
            if np.isfinite(value) and value <= cost_limit:
                x[row] = col
                y[col] = row
                total += float(value)
        return total, x, y

    lap_module.lapjv = lapjv
    sys.modules["lap"] = lap_module

    bbox_module = types.ModuleType("cython_bbox")

    def bbox_overlaps(boxes_a, boxes_b):
        a = np.asarray(boxes_a, dtype=np.float64)
        b = np.asarray(boxes_b, dtype=np.float64)
        if len(a) == 0 or len(b) == 0:
            return np.zeros((len(a), len(b)), dtype=np.float64)
        left_top = np.maximum(a[:, None, :2], b[None, :, :2])
        right_bottom = np.minimum(a[:, None, 2:], b[None, :, 2:])
        extent = np.maximum(right_bottom - left_top, 0.0)
        intersection = extent[..., 0] * extent[..., 1]
        area_a = np.maximum(a[:, 2] - a[:, 0], 0.0) * np.maximum(
            a[:, 3] - a[:, 1], 0.0
        )
        area_b = np.maximum(b[:, 2] - b[:, 0], 0.0) * np.maximum(
            b[:, 3] - b[:, 1], 0.0
        )
        return intersection / (
            area_a[:, None] + area_b[None, :] - intersection + 1e-12
        )

    bbox_module.bbox_overlaps = bbox_overlaps
    sys.modules["cython_bbox"] = bbox_module
    return {
        "lap": "scipy.optimize.linear_sum_assignment adapter with cost-limit filtering",
        "cython_bbox": "vectorized NumPy IoU adapter",
    }


def run_yolo26(bundle: Path, artifacts: Path, warmups: int, runs: int):
    runtime_dir = bundle / "rdk_model_zoo" / "samples" / "vision" / "ultralytics_yolo26" / "runtime" / "python"
    sys.path.insert(0, str(runtime_dir))
    from yolo26_det import YOLO26Detect, YOLO26DetectConfig

    model_path = bundle / "models" / "yolo26n_detect_nashe_640x640_nv12.hbm"
    image_path = bundle / "data" / "bus.jpg"
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("failed to read YOLO26 fixture")
    model = YOLO26Detect(YOLO26DetectConfig(model_path=str(model_path)))
    model.set_scheduling_params(priority=0, bpu_cores=[0])
    prepared = model.pre_process(image)
    bpu_timing = benchmark(lambda: model.model.run(prepared), warmups, runs)
    boxes, scores, class_ids = model.predict(image)
    rendered = image.copy()
    for box, score, class_id in zip(boxes, scores, class_ids):
        x1, y1, x2, y2 = (int(value) for value in box)
        cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            rendered,
            f"{int(class_id)}:{float(score):.2f}",
            (x1, max(15, y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
        )
    output_path = artifacts / "yolo26_detect.png"
    if not cv2.imwrite(str(output_path), rendered):
        raise RuntimeError("failed to write YOLO26 result")
    checks = {
        "detections_present": len(boxes) > 0,
        "finite": bool(np.isfinite(boxes).all() and np.isfinite(scores).all()),
        "parallel_lengths": len(boxes) == len(scores) == len(class_ids),
        "measurement_counts": warmups >= 20 and runs >= 100,
    }
    return {
        "name": "yolo26_detect",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "model_sha256": sha256(model_path),
        "input_sha256": sha256(image_path),
        "detections": int(len(boxes)),
        "class_ids": sorted({int(value) for value in class_ids}),
        "score_max": float(np.max(scores)) if len(scores) else None,
        "bpu_runtime_api_timing": bpu_timing,
        "artifact": output_path.name,
        "artifact_sha256": sha256(output_path),
    }


def run_depth(bundle: Path, artifacts: Path, warmups: int, runs: int):
    import hbm_runtime
    from utils.py_utils import nn_math, preprocess

    model_path = bundle / "models" / "depth_any.hbm"
    image_path = bundle / "data" / "furseal.jpg"
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("failed to read Depth Anything fixture")
    runtime = hbm_runtime.HB_HBMRuntime(str(model_path))
    model_name = runtime.model_names[0]
    input_name = runtime.input_names[model_name][0]
    input_shape = runtime.input_shapes[model_name][input_name]
    height, width = int(input_shape[2]), int(input_shape[3])
    resized = preprocess.resized_image(image, width, height, resize_type=0)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normalized = nn_math.zscore_normalize_lastdim(rgb)
    tensor = np.ascontiguousarray(
        np.transpose(normalized, (2, 0, 1))[None].astype(np.float32)
    )
    prepared = {model_name: {input_name: tensor}}
    bpu_timing = benchmark(lambda: runtime.run(prepared), warmups, runs)
    raw = only_output(runtime.run(prepared)).astype(np.float32, copy=False).squeeze()
    depth = cv2.resize(raw, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
    depth_range = float(np.max(depth) - np.min(depth))
    normalized_depth = np.zeros_like(depth, dtype=np.uint8)
    if depth_range > 0:
        normalized_depth = np.clip(
            (depth - np.min(depth)) / depth_range * 255.0, 0, 255
        ).astype(np.uint8)
    color = cv2.applyColorMap(normalized_depth, cv2.COLORMAP_INFERNO)
    output_path = artifacts / "depth_anything_v2.png"
    if not cv2.imwrite(str(output_path), color):
        raise RuntimeError("failed to write depth result")
    checks = {
        "finite": bool(np.isfinite(raw).all()),
        "nonconstant_depth": depth_range > 1e-6,
        "rank_after_squeeze_is_2": raw.ndim == 2,
        "measurement_counts": warmups >= 20 and runs >= 100,
    }
    return {
        "name": "depth_anything_v2",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "model_sha256": sha256(model_path),
        "input_sha256": sha256(image_path),
        "input_shape": list(tensor.shape),
        "raw_output_shape": list(raw.shape),
        "raw_min": float(np.min(raw)),
        "raw_max": float(np.max(raw)),
        "bpu_runtime_api_timing": bpu_timing,
        "postprocess_adapter": "OpenCV bilinear resize replaces upstream torch.interpolate; inference tensors are unchanged",
        "artifact": output_path.name,
        "artifact_sha256": sha256(output_path),
    }


def run_pointnet(bundle: Path, artifacts: Path, warmups: int, runs: int):
    runtime_dir = bundle / "rdk_model_zoo" / "samples" / "vision" / "pointnet" / "runtime" / "python"
    sys.path.insert(0, str(runtime_dir))
    from pointnet import PointNet, PointNetConfig

    model_path = bundle / "models" / "pointnet.hbm"
    points_path = bundle / "data" / "chair.pts"
    model = PointNet(PointNetConfig(model_path=str(model_path)))
    model.set_scheduling_params(priority=0, bpu_cores=[0])
    points = PointNet.load_point_cloud(str(points_path))
    prepared = model.pre_process(points)
    bpu_timing = benchmark(lambda: model.forward(prepared), warmups, runs)
    labels = model.predict(points)
    output_path = artifacts / "pointnet_labels.npy"
    np.save(output_path, labels, allow_pickle=False)
    counts = {str(index): int(np.count_nonzero(labels == index)) for index in range(4)}
    checks = {
        "point_count_preserved": len(labels) == len(points),
        "labels_in_contract": bool(np.all((labels >= 0) & (labels < 4))),
        "multiple_parts_present": int(np.count_nonzero(np.asarray(list(counts.values())))) >= 2,
        "measurement_counts": warmups >= 20 and runs >= 100,
    }
    return {
        "name": "pointnet_part_segmentation",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "model_sha256": sha256(model_path),
        "input_sha256": sha256(points_path),
        "points": int(len(points)),
        "part_counts": counts,
        "bpu_runtime_api_timing": bpu_timing,
        "artifact": output_path.name,
        "artifact_sha256": sha256(output_path),
    }


def run_bytetrack(bundle: Path, artifacts: Path, warmups: int, runs: int, max_frames: int):
    compatibility = install_bytetrack_compatibility_modules()
    sample = bundle / "rdk_model_zoo" / "samples" / "vision" / "bytetrack"
    sys.path.insert(0, str(sample / "3rdparty"))
    sys.path.insert(0, str(sample / "runtime" / "python"))
    from bytetrack import ByteTrack, ByteTrackConfig

    model_path = bundle / "models" / "yolov5x_672x672_nv12.hbm"
    video_path = bundle / "data" / "track_test.mp4"
    tracker = ByteTrack(
        ByteTrackConfig(
            model_path=str(model_path),
            score_thres=0.25,
            nms_thres=0.45,
            track_thresh=0.3,
        )
    )
    tracker.set_scheduling_params(priority=0, bpu_cores=[0])
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("failed to open ByteTrack video fixture")
    frame_total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    ok, first = capture.read()
    if not ok:
        raise RuntimeError("ByteTrack video has no frames")
    prepared = tracker.detector.pre_process(first)
    detector_timing = benchmark(
        lambda: tracker.detector.model.run(prepared), warmups, runs
    )
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame_times = []
    observed_ids = set()
    track_observations = 0
    frames_with_tracks = 0
    first_rendered = None
    last_rendered = None
    processed = 0
    while processed < max_frames:
        ok, frame = capture.read()
        if not ok:
            break
        started = time.perf_counter_ns()
        tracks = tracker.predict(frame)
        frame_times.append((time.perf_counter_ns() - started) / 1_000_000.0)
        rendered = frame.copy()
        if tracks:
            frames_with_tracks += 1
        for track in tracks:
            observed_ids.add(int(track.track_id))
            track_observations += 1
            x1, y1, width, height = (float(value) for value in track.tlwh)
            x2, y2 = x1 + width, y1 + height
            cv2.rectangle(
                rendered,
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                (0, 255, 255),
                2,
            )
            cv2.putText(
                rendered,
                f"ID:{int(track.track_id)}",
                (int(x1), max(15, int(y1) - 3)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 255, 255),
                1,
            )
        if processed == 0:
            first_rendered = rendered
        last_rendered = rendered
        processed += 1
    capture.release()
    if first_rendered is not None:
        cv2.imwrite(str(artifacts / "bytetrack_first.png"), first_rendered)
    if last_rendered is not None:
        cv2.imwrite(str(artifacts / "bytetrack_last.png"), last_rendered)
    timing_values = np.asarray(frame_times, dtype=np.float64)
    e2e_timing = {
        "frames": len(frame_times),
        "p50_ms": float(np.percentile(timing_values, 50, method="linear")),
        "p95_ms": float(np.percentile(timing_values, 95, method="linear")),
        "p99_ms": float(np.percentile(timing_values, 99, method="linear")),
        "max_ms": float(np.max(timing_values)),
        "mean_ms": float(np.mean(timing_values)),
    }
    minimum_frames = min(60, frame_total, max_frames)
    checks = {
        "minimum_frames_processed": processed >= minimum_frames,
        "tracks_present": track_observations > 0,
        "track_ids_present": len(observed_ids) > 0,
        "finite_timing": bool(np.isfinite(timing_values).all()),
        "measurement_counts": warmups >= 20 and runs >= 100,
    }
    return {
        "name": "bytetrack",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "model_sha256": sha256(model_path),
        "input_sha256": sha256(video_path),
        "video_frames_reported": frame_total,
        "frames_processed": processed,
        "frames_with_tracks": frames_with_tracks,
        "track_observations": track_observations,
        "unique_track_ids": sorted(observed_ids),
        "detector_bpu_runtime_api_timing": detector_timing,
        "pipeline_e2e_timing": e2e_timing,
        "compatibility_adapter": compatibility,
        "artifacts": ["bytetrack_first.png", "bytetrack_last.png"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=20)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--bytetrack-max-frames", type=int, default=120)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    manifest, manifest_hash = verify_bundle(bundle)
    sys.path.insert(0, str(bundle / "rdk_model_zoo"))
    artifacts = args.output.resolve().parent / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    cases = [
        ("yolo26_detect", lambda: run_yolo26(bundle, artifacts, args.warmups, args.runs)),
        ("depth_anything_v2", lambda: run_depth(bundle, artifacts, args.warmups, args.runs)),
        ("pointnet_part_segmentation", lambda: run_pointnet(bundle, artifacts, args.warmups, args.runs)),
        (
            "bytetrack",
            lambda: run_bytetrack(
                bundle,
                artifacts,
                args.warmups,
                args.runs,
                args.bytetrack_max_frames,
            ),
        ),
    ]
    results = []
    for name, callable_ in cases:
        try:
            results.append(callable_())
        except Exception as exc:
            results.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    pass_count = sum(item["status"] == "PASS" for item in results)
    output = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "S100 D4 official Model Zoo offline smoke; no sensors, robot, or control output",
        "source": manifest["source"],
        "bundle_manifest_sha256": manifest_hash,
        "summary": {
            "total": len(results),
            "pass": pass_count,
            "fail": len(results) - pass_count,
        },
        "models": results,
        "external_network": False,
        "external_device_access": False,
        "control_output": False,
        "bpu_runtime_access": True,
        "result": "PASS" if pass_count == len(results) else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    print(json.dumps(output["summary"], ensure_ascii=False))
    if output["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
