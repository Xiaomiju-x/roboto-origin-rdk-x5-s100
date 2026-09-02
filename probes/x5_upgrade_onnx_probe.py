#!/usr/bin/env python3
"""Execute the project-owned temporal ONNX model on a saved nonzero vector."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=100)
    args = parser.parse_args()

    vectors = np.load(args.vectors)
    input_value = vectors["bev_history"].astype(np.float32)
    expected = [vectors["occupancy_logits"], vectors["flow"], vectors["uncertainty_logits"]]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    session = ort.InferenceSession(str(args.model), options, providers=["CPUExecutionProvider"])
    feed = {session.get_inputs()[0].name: input_value}
    for _ in range(5):
        session.run(None, feed)
    samples_ms: list[float] = []
    outputs = []
    for _ in range(args.repeats):
        start = time.perf_counter_ns()
        outputs = session.run(None, feed)
        samples_ms.append((time.perf_counter_ns() - start) / 1_000_000.0)
    comparisons = []
    for metadata, actual, reference in zip(session.get_outputs(), outputs, expected):
        difference = np.abs(actual.astype(np.float64) - reference.astype(np.float64))
        comparisons.append(
            {
                "name": metadata.name,
                "shape": list(actual.shape),
                "finite": bool(np.isfinite(actual).all()),
                "max_abs_error": float(difference.max()),
                "mean_abs_error": float(difference.mean()),
                "allclose_rtol_1e-4_atol_1e-5": bool(np.allclose(actual, reference, rtol=1.0e-4, atol=1.0e-5)),
            }
        )
    passed = all(item["finite"] and item["allclose_rtol_1e-4_atol_1e-5"] for item in comparisons)
    payload = {
        "schema_version": 1,
        "scope": "X5 CPU synthetic ONNX inference; no ROS or devices",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine()},
        "onnxruntime": {"version": ort.__version__, "providers": session.get_providers()},
        "model": {"path": str(args.model), "sha256": sha256(args.model)},
        "vectors": {"path": str(args.vectors), "sha256": sha256(args.vectors)},
        "input_nonzero": bool(np.any(input_value != 0.0)),
        "latency_ms": {
            "repeats": args.repeats,
            "min": min(samples_ms),
            "median": statistics.median(samples_ms),
            "mean": statistics.fmean(samples_ms),
            "max": max(samples_ms),
        },
        "outputs": comparisons,
        "result": "PASS" if passed else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "output": str(args.output), "latency_ms": payload["latency_ms"]}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
