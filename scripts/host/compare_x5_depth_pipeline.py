#!/usr/bin/env python3

"""Compare X5 official depth_node output with a host OpenCV/ONNX reference."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import torch
import onnxruntime as ort

if hasattr(ort, "preload_dlls"):
    ort.preload_dlls()


WIDTH = 480
HEIGHT = 270


def synthetic_depth_mm() -> np.ndarray:
    rows = np.arange(HEIGHT, dtype=np.uint32)[:, None]
    cols = np.arange(WIDTH, dtype=np.uint32)[None, :]
    values = 250 + ((rows * 11 + cols * 7) % 3100)
    values[((rows * 13 + cols * 5) % 97) == 0] = 0
    return values.astype(np.uint16)


def sha256_f32(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<f4").tobytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--x5-result", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rtol", type=float, default=1.0e-3)
    parser.add_argument("--atol", type=float, default=1.0e-4)
    args = parser.parse_args()

    x5 = json.loads(args.x5_result.read_text(encoding="utf-8"))
    x5_values = np.asarray(x5["first_fixed_output"]["values"], dtype=np.float32)

    depth_m = synthetic_depth_mm().astype(np.float32) * np.float32(0.001)
    positive = depth_m > 0.0
    depth_m[positive] = np.clip(depth_m[positive], 0.0, 2.5)
    resized = cv2.resize(depth_m, (64, 36), interpolation=cv2.INTER_LINEAR)
    cropped = resized[18:36, 16:48]
    normalized = np.clip(cropped / np.float32(2.5), 0.0, 1.0).astype(np.float32)
    encoder_input = np.stack([normalized] * 8, axis=0)[None, ...]

    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    session = ort.InferenceSession(str(args.model), providers=providers)
    host_values = session.run(None, {session.get_inputs()[0].name: encoder_input})[0].reshape(-1).astype(np.float32)
    difference = np.abs(host_values - x5_values)
    passed = (
        x5.get("status") == "PASS"
        and x5_values.shape == (128,)
        and host_values.shape == (128,)
        and bool(np.isfinite(host_values).all())
        and bool(np.allclose(host_values, x5_values, rtol=args.rtol, atol=args.atol))
    )
    payload = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "PASS" if passed else "FAIL",
        "scope": "host OpenCV/ONNX reference versus official X5 C++ depth_node",
        "sources": {
            "x5_result": str(args.x5_result.resolve()),
            "model": str(args.model.resolve()),
        },
        "runtime": {
            "opencv": cv2.__version__,
            "onnxruntime": ort.__version__,
            "torch": torch.__version__,
            "active_providers": session.get_providers(),
        },
        "contract": {
            "raw_shape": list(depth_m.shape),
            "resized_shape": list(resized.shape),
            "crop_shape": list(cropped.shape),
            "encoder_input_shape": list(encoder_input.shape),
            "encoder_output_shape": list(host_values.shape),
            "encoder_input_min": float(encoder_input.min()),
            "encoder_input_max": float(encoder_input.max()),
        },
        "comparison": {
            "rtol": args.rtol,
            "atol": args.atol,
            "max_abs_diff": float(difference.max()) if difference.size else None,
            "mean_abs_diff": float(difference.mean()) if difference.size else None,
            "x5_sha256_float32_le": sha256_f32(x5_values),
            "host_sha256_float32_le": sha256_f32(host_values),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
