#!/usr/bin/env python3
"""Compare deterministic host CPU outputs with recorded X5 ORT outputs."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort


def deterministic_input(shape: tuple[int, ...]) -> np.ndarray:
    count = int(np.prod(shape))
    indices = np.arange(count, dtype=np.float64)
    values = 0.1 * np.sin(indices * 0.013) + 0.05 * np.cos(indices * 0.007)
    return values.astype(np.float32).reshape(shape)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--x5", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--repeats", type=int, default=50)
    args = parser.parse_args()

    repo = args.repo.resolve()
    x5 = json.loads(args.x5.read_text(encoding="utf-8"))
    if x5.get("result") != "PASS" or x5.get("input_mode") != "synthetic_deterministic_trigonometric_float32":
        raise ValueError("X5 evidence is not a passing deterministic-input report")

    model_by_label = {
        "policy": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy.onnx",
        "policy_amp": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_amp.onnx",
        "policy_attn_enc": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_attn_enc.onnx",
        "policy_dance0": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_dance0.onnx",
        "policy_dance1": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_dance1.onnx",
        "policy_getup": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_getup.onnx",
        "policy_interrupt": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_interrupt.onnx",
        "policy_parkour": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_parkour.onnx",
        "policy_wave": repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_wave.onnx",
        "depth_encoder": repo / "upstream/roboparty_deploy/src/camera/models/encoder.onnx",
    }

    results = []
    passed = True
    for board_model in x5["models"]:
        label = board_model["label"]
        model_path = model_by_label[label]
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        input_meta = session.get_inputs()[0]
        shape = tuple(int(value) if isinstance(value, int) and value > 0 else 1 for value in input_meta.shape)
        input_data = deterministic_input(shape)
        feed = {input_meta.name: input_data}
        for _ in range(5):
            session.run(None, feed)
        samples_ms = []
        output = None
        for _ in range(args.repeats):
            start = time.perf_counter_ns()
            output = session.run(None, feed)[0]
            samples_ms.append((time.perf_counter_ns() - start) / 1_000_000.0)
        assert output is not None
        host_values = output.reshape(-1).astype(np.float64)
        board_values = np.asarray(board_model["output_values"], dtype=np.float64)
        if host_values.shape != board_values.shape:
            raise ValueError(f"Output shape mismatch for {label}: {host_values.shape} vs {board_values.shape}")
        abs_error = np.abs(host_values - board_values)
        denom = np.maximum(np.abs(host_values), 1.0e-8)
        parity = bool(np.allclose(host_values, board_values, rtol=1.0e-3, atol=1.0e-4))
        passed = passed and parity and bool(np.isfinite(host_values).all())
        results.append(
            {
                "label": label,
                "input_shape": list(shape),
                "output_elements": int(host_values.size),
                "finite": bool(np.isfinite(host_values).all()),
                "max_abs_error": float(np.max(abs_error)),
                "mean_abs_error": float(np.mean(abs_error)),
                "max_rel_error": float(np.max(abs_error / denom)),
                "allclose_rtol_1e-3_atol_1e-4": parity,
                "host_latency_ms": {
                    "repeats": args.repeats,
                    "min": min(samples_ms),
                    "median": statistics.median(samples_ms),
                    "mean": statistics.fmean(samples_ms),
                    "max": max(samples_ms),
                },
                "x5_latency_ms": board_model["elapsed_ms"],
            }
        )

    report = {
        "schema_version": 1,
        "scope": "host x86_64 vs X5 aarch64 deterministic-input ONNX CPU parity; no devices",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "result": "PASS" if passed else "FAIL",
        "input_mode": x5["input_mode"],
        "host": {"platform": platform.platform(), "onnxruntime": ort.__version__},
        "x5": {
            "hostname": x5.get("hostname"),
            "machine": x5.get("machine"),
            "onnxruntime": x5.get("onnxruntime_version"),
            "source_evidence": args.x5.as_posix(),
        },
        "models": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "models": len(results), "output": str(args.output)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
