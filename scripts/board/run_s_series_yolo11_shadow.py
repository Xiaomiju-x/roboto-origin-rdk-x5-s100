#!/usr/bin/env python3
"""Run file-only YOLO11n detection on S100/S600 and emit a shadow event.

Model binaries and evaluation images are supplied on the target board and are
not redistributed by this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from roboto_upgrade.yolo_shadow import make_shadow_decision, normalize_pixel_detections

EXPECTED = {
    "s100": {
        "soc": "S100",
        "model_sha256": "50f59e31473a3f496060f14ad947231fbebe04ddc9b1cbf0efa0c499253907b4",
        "model_name_fragment": "yolo11n_detect_nashe",
    },
    "s600": {
        "soc": "S600",
        "model_sha256": "45b554d97521524ffbec6016ece8a4e87f99b100a3624724e9ed2ce7747ff5f8",
        "model_name_fragment": "yolo11n_detect_nashp",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resized_image(image: np.ndarray, width: int, height: int) -> np.ndarray:
    source_h, source_w = image.shape[:2]
    scale = min(height / source_h, width / source_w)
    resized = cv2.resize(image, (int(source_w * scale), int(source_h * scale)))
    pad_w = width - resized.shape[1]
    pad_h = height - resized.shape[0]
    return cv2.copyMakeBorder(
        resized,
        pad_h // 2,
        pad_h - pad_h // 2,
        pad_w // 2,
        pad_w - pad_w // 2,
        cv2.BORDER_CONSTANT,
        value=(127, 127, 127),
    )


def bgr_to_nv12_planes(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height, width = image.shape[:2]
    area = height * width
    i420 = cv2.cvtColor(image, cv2.COLOR_BGR2YUV_I420).reshape(-1)
    y = i420[:area].reshape(1, height, width, 1)
    u = i420[area : area + area // 4].reshape(height // 2, width // 2)
    v = i420[area + area // 4 :].reshape(height // 2, width // 2)
    return y, np.stack((u, v), axis=-1)[None, ...]


def dequantize(tensor: np.ndarray, quant: Any) -> np.ndarray:
    quant_name = getattr(getattr(quant, "quant_type", None), "name", str(getattr(quant, "quant_type", "")))
    if quant_name not in {"SCALE", "1"}:
        return tensor.astype(np.float32, copy=False)
    scale = np.asarray(quant.scale, dtype=np.float32)
    zero = np.asarray(quant.zero_point, dtype=np.float32)
    if scale.size <= 1:
        return (tensor.astype(np.float32) - (float(zero.reshape(-1)[0]) if zero.size else 0.0)) * float(scale.reshape(-1)[0])
    shape = [1] * tensor.ndim
    shape[int(quant.axis)] = scale.size
    zero_value = zero.reshape(shape) if zero.size == scale.size else 0.0
    return (tensor.astype(np.float32) - zero_value) * scale.reshape(shape)


def softmax(values: np.ndarray, axis: int) -> np.ndarray:
    shifted = values - np.max(values, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=axis, keepdims=True)


def decode_boxes(box_tensor: np.ndarray, indices: np.ndarray, grid: int, stride: int) -> np.ndarray:
    selected = box_tensor.reshape(-1, 64)[indices].reshape(-1, 4, 16)
    distances = np.sum(softmax(selected, axis=2) * np.arange(16, dtype=np.float32), axis=2)
    x = np.tile(np.arange(grid, dtype=np.float32) + 0.5, grid)
    y = np.repeat(np.arange(grid, dtype=np.float32) + 0.5, grid)
    anchors = np.stack((x, y), axis=1)[indices]
    return np.hstack((anchors - distances[:, :2], anchors + distances[:, 2:])) * stride


def nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, threshold: float) -> list[int]:
    keep: list[int] = []
    for class_id in np.unique(classes):
        group = np.flatnonzero(classes == class_id)
        order = group[np.argsort(scores[group])[::-1]]
        while order.size:
            current = int(order[0])
            keep.append(current)
            if order.size == 1:
                break
            rest = order[1:]
            left = np.maximum(boxes[current, 0], boxes[rest, 0])
            top = np.maximum(boxes[current, 1], boxes[rest, 1])
            right = np.minimum(boxes[current, 2], boxes[rest, 2])
            bottom = np.minimum(boxes[current, 3], boxes[rest, 3])
            intersection = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
            area_a = (boxes[current, 2] - boxes[current, 0]) * (boxes[current, 3] - boxes[current, 1])
            area_b = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            iou = intersection / (area_a + area_b - intersection + 1e-9)
            order = rest[iou < threshold]
    return keep


def decode(
    raw: dict[str, np.ndarray],
    output_names: list[str],
    quants: dict[str, Any],
    image_width: int,
    image_height: int,
    score_threshold: float,
) -> list[dict]:
    outputs = {name: dequantize(raw[name], quants[name]) for name in output_names}
    all_boxes: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []
    all_classes: list[np.ndarray] = []
    logit_threshold = -math.log(1.0 / score_threshold - 1.0)
    for level, stride in enumerate((8, 16, 32)):
        cls = outputs[output_names[level * 2]].reshape(-1, 80)
        logits = np.max(cls, axis=1)
        indices = np.flatnonzero(logits >= logit_threshold)
        if not indices.size:
            continue
        all_scores.append(1.0 / (1.0 + np.exp(-logits[indices])))
        all_classes.append(np.argmax(cls[indices], axis=1).astype(np.int32))
        all_boxes.append(decode_boxes(outputs[output_names[level * 2 + 1]], indices, 640 // stride, stride))
    if not all_boxes:
        return []
    boxes = np.concatenate(all_boxes).astype(np.float32)
    scores = np.concatenate(all_scores).astype(np.float32)
    classes = np.concatenate(all_classes).astype(np.int32)
    keep = nms(boxes, scores, classes, 0.70)
    boxes = boxes[keep]
    scores = scores[keep]
    classes = classes[keep]
    scale = min(640 / image_width, 640 / image_height)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - (640 - image_width * scale) / 2) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - (640 - image_height * scale) / 2) / scale
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, image_width)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, image_height)
    rows = []
    for box, score, class_id in zip(boxes, scores, classes):
        if box[2] > box[0] and box[3] > box[1] and np.isfinite(box).all() and np.isfinite(score):
            rows.append(
                {
                    "class_id": int(class_id),
                    "score": float(score),
                    "x1": float(box[0]),
                    "y1": float(box[1]),
                    "x2": float(box[2]),
                    "y2": float(box[3]),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("s100", "s600"), required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timed", type=int, default=10)
    args = parser.parse_args()
    expected = EXPECTED[args.platform]
    if os.environ.get("ROS_DOMAIN_ID") != "42":
        raise RuntimeError("ROS_DOMAIN_ID must be 42")
    if Path("/sys/class/boardinfo/soc_name").read_text().strip() != expected["soc"]:
        raise RuntimeError("board identity mismatch")
    if sha256(args.model) != expected["model_sha256"]:
        raise RuntimeError("model hash mismatch")
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise RuntimeError("fixture image is empty or unreadable")

    import hbm_runtime

    runtime = hbm_runtime.HB_HBMRuntime([str(args.model)])
    if len(runtime.model_names) != 1:
        raise RuntimeError("expected exactly one model")
    model_name = runtime.model_names[0]
    if expected["model_name_fragment"] not in model_name:
        raise RuntimeError("runtime model name mismatch")
    input_names = list(runtime.input_names[model_name])
    output_names = list(runtime.output_names[model_name])
    if len(input_names) != 2 or len(output_names) != 6:
        raise RuntimeError("unexpected YOLO11 tensor contract")
    resized = resized_image(image, 640, 640)
    y, uv = bgr_to_nv12_planes(resized)
    inputs = {model_name: {input_names[0]: y, input_names[1]: uv}}
    runtime.set_scheduling_params(
        priority={model_name: 5},
        bpu_cores={model_name: [0]},
    )
    runtime.run(inputs)
    runtime.run(inputs)
    latencies = []
    hashes = []
    final_raw = None
    for index in range(args.timed):
        start = time.perf_counter()
        result = runtime.run(inputs)[model_name]
        latencies.append((time.perf_counter() - start) * 1000.0)
        digest = hashlib.sha256()
        for name in output_names:
            array = np.ascontiguousarray(result[name])
            if not np.isfinite(array).all():
                raise RuntimeError("non-finite runtime output")
            digest.update(name.encode())
            digest.update(array.tobytes())
        hashes.append(digest.hexdigest())
        final_raw = result
    if len(set(hashes)) != 1 or final_raw is None:
        raise RuntimeError("runtime outputs are not deterministic")
    detections = decode(
        final_raw,
        output_names,
        runtime.output_quants[model_name],
        image.shape[1],
        image.shape[0],
        0.25,
    )
    if not detections:
        raise RuntimeError("no valid detections after post-processing")
    normalized = normalize_pixel_detections(detections, image_width=image.shape[1], image_height=image.shape[0])
    decision = make_shadow_decision(normalized, platform=args.platform, frame_id=0)
    payload = {
        "schema": "roboto_origin.s_series_yolo11_shadow.v1",
        "status": f"{args.platform.upper()}_YOLO11_BPU_SHADOW_FILE_PASS",
        "platform": args.platform,
        "execution_unit": "BPU_DETECT_CPU_POSTPROCESS",
        "model": {
            "name": model_name,
            "sha256": sha256(args.model),
            "redistributed": False,
        },
        "fixture": {"sha256": sha256(args.image), "redistributed": False},
        "timed_runs": args.timed,
        "latency_ms": {
            "p50": statistics.median(latencies),
            "min": min(latencies),
            "max": max(latencies),
        },
        "raw_output_sha256": hashes[0],
        "deterministic": True,
        "detections": detections,
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
    print(json.dumps({"status": payload["status"], "detections": len(detections)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
