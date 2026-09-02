#!/usr/bin/env python3
"""Validate the project-owned U1 temporal BEV model on S100 CPU and BPU.

This is an offline, file-only probe.  It never opens a camera, ROS topic, CAN,
serial port, or actuator device.
"""

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
from hbm_runtime import HB_HBMRuntime


OUTPUT_NAMES = ("occupancy_logits", "flow", "uncertainty_logits")
MEAN_COSINE_TARGET = 0.999
PER_SAMPLE_COSINE_FLOOR = 0.99


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), q, method="linear"))


def cosine(reference: np.ndarray, observed: np.ndarray) -> float:
    lhs = reference.astype(np.float64, copy=False).reshape(-1)
    rhs = observed.astype(np.float64, copy=False).reshape(-1)
    denominator = float(np.linalg.norm(lhs) * np.linalg.norm(rhs))
    if denominator <= 1e-12:
        return 1.0 if np.allclose(lhs, rhs, atol=1e-7, rtol=1e-7) else 0.0
    return float(np.dot(lhs, rhs) / denominator)


def benchmark(callable_, warmups: int, runs: int) -> dict[str, float | int]:
    for _ in range(warmups):
        callable_()
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter_ns()
        callable_()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return {
        "warmups": warmups,
        "runs": runs,
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
        "p99_ms": percentile(samples, 99),
        "max_ms": max(samples),
        "mean_ms": statistics.fmean(samples),
    }


def unpack_hbm_outputs(
    result: dict[str, dict[str, np.ndarray]],
    expected_shapes: dict[str, tuple[int, ...]],
) -> dict[str, np.ndarray]:
    if len(result) != 1:
        raise RuntimeError(f"expected one HBM model result, got {list(result)}")
    raw = next(iter(result.values()))
    arrays = {name: np.asarray(value) for name, value in raw.items()}
    if all(name in arrays for name in OUTPUT_NAMES):
        return {name: arrays[name] for name in OUTPUT_NAMES}

    # Some runtime versions decorate output names.  All three U1 output shapes
    # are unique, so shape matching remains deterministic and auditable.
    mapped: dict[str, np.ndarray] = {}
    unused = dict(arrays)
    for expected_name in OUTPUT_NAMES:
        shape = expected_shapes[expected_name]
        matches = [name for name, value in unused.items() if tuple(value.shape) == shape]
        if len(matches) != 1:
            raise RuntimeError(
                f"cannot uniquely map {expected_name} shape={shape}; "
                f"runtime outputs={[(name, list(value.shape)) for name, value in arrays.items()]}"
            )
        runtime_name = matches[0]
        mapped[expected_name] = unused.pop(runtime_name)
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=20)
    parser.add_argument("--runs", type=int, default=200)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    manifest_path = bundle / "deployment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_path = bundle / manifest["artifacts"]["onnx"]["path"]
    tests_path = bundle / manifest["artifacts"]["test_inputs"]["path"]
    x5_fixture_path = bundle / manifest["artifacts"]["x5_fixture"]["path"]
    hbm_path = bundle / manifest["artifacts"]["hbm"]["path"]
    for key, path in {
        "onnx": model_path,
        "test_inputs": tests_path,
        "x5_fixture": x5_fixture_path,
        "hbm": hbm_path,
    }.items():
        expected_hash = manifest["artifacts"][key]["sha256"]
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise RuntimeError(f"{key} hash mismatch: {actual_hash} != {expected_hash}")

    tests = np.load(tests_path, allow_pickle=False).astype(np.float32, copy=False)
    if tests.shape != (128, 4, 32, 32):
        raise RuntimeError(f"unexpected test tensor shape: {tests.shape}")
    x5 = np.load(x5_fixture_path, allow_pickle=False)
    x5_input = x5["bev_history"].astype(np.float32, copy=False)
    if x5_input.shape != (1, 4, 32, 32):
        raise RuntimeError(f"unexpected X5 fixture shape: {x5_input.shape}")
    fixture_first_exact = bool(np.array_equal(tests[0:1], x5_input))

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    cpu = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    cpu_input_name = cpu.get_inputs()[0].name
    cpu_output_names = [item.name for item in cpu.get_outputs()]
    if tuple(cpu_output_names) != OUTPUT_NAMES:
        raise RuntimeError(f"unexpected ONNX outputs: {cpu_output_names}")
    expected_shapes = {
        item.name: tuple(int(value) for value in item.shape) for item in cpu.get_outputs()
    }

    cpu_results: dict[str, list[np.ndarray]] = {name: [] for name in OUTPUT_NAMES}
    for sample in tests:
        values = cpu.run(None, {cpu_input_name: np.ascontiguousarray(sample[None])})
        for name, value in zip(OUTPUT_NAMES, values):
            cpu_results[name].append(np.asarray(value, dtype=np.float32))

    runtime = HB_HBMRuntime(str(hbm_path))
    bpu_results: dict[str, list[np.ndarray]] = {name: [] for name in OUTPUT_NAMES}
    for sample in tests:
        values = unpack_hbm_outputs(
            runtime.run(np.ascontiguousarray(sample[None])), expected_shapes
        )
        for name in OUTPUT_NAMES:
            value = np.asarray(values[name], dtype=np.float32)
            if tuple(value.shape) != expected_shapes[name]:
                raise RuntimeError(
                    f"{name} output shape mismatch: {value.shape} != {expected_shapes[name]}"
                )
            bpu_results[name].append(value)

    output_metrics: list[dict[str, object]] = []
    all_similarities: list[float] = []
    all_finite = True
    for name in OUTPUT_NAMES:
        cpu_stack = np.concatenate(cpu_results[name], axis=0)
        bpu_stack = np.concatenate(bpu_results[name], axis=0)
        similarities = [
            cosine(cpu_results[name][index], bpu_results[name][index])
            for index in range(len(tests))
        ]
        finite = bool(np.isfinite(cpu_stack).all() and np.isfinite(bpu_stack).all())
        all_finite = all_finite and finite
        all_similarities.extend(similarities)
        x5_expected = x5[name].astype(np.float32, copy=False)
        x5_cpu = cpu_results[name][0]
        x5_bpu = bpu_results[name][0]
        output_metrics.append(
            {
                "name": name,
                "shape": list(expected_shapes[name]),
                "finite": finite,
                "cosine_mean": float(np.mean(similarities)),
                "cosine_min": min(similarities),
                "cosine_max": max(similarities),
                "max_abs_error_cpu_vs_bpu": float(np.max(np.abs(cpu_stack - bpu_stack))),
                "x5_fixture_cpu_allclose_rtol_1e_4_atol_1e_5": bool(
                    np.allclose(x5_cpu, x5_expected, rtol=1e-4, atol=1e-5)
                ),
                "x5_fixture_cpu_max_abs_error": float(
                    np.max(np.abs(x5_cpu - x5_expected))
                ),
                "x5_fixture_bpu_cosine": cosine(x5_expected, x5_bpu),
                "x5_fixture_bpu_max_abs_error": float(
                    np.max(np.abs(x5_bpu - x5_expected))
                ),
            }
        )

    benchmark_sample = np.ascontiguousarray(tests[5:6])
    cpu_timing = benchmark(
        lambda: cpu.run(None, {cpu_input_name: benchmark_sample}),
        args.warmups,
        args.runs,
    )
    bpu_timing = benchmark(lambda: runtime.run(benchmark_sample), args.warmups, args.runs)
    checks = {
        "artifact_hashes_match": True,
        "same_fixture_hash_as_x5": manifest["artifacts"]["x5_fixture"]["sha256"]
        == manifest["x5_baseline"]["fixture_sha256"],
        "x5_fixture_is_test_zero_exact": fixture_first_exact,
        "x5_fixture_cpu_outputs_portable": all(
            bool(item["x5_fixture_cpu_allclose_rtol_1e_4_atol_1e_5"])
            for item in output_metrics
        ),
        "finite": all_finite,
        "mean_cosine_target": float(np.mean(all_similarities)) >= MEAN_COSINE_TARGET,
        "per_sample_output_cosine_floor": min(all_similarities)
        >= PER_SAMPLE_COSINE_FLOOR,
        "warmup_and_measurement_counts": args.warmups >= 20 and args.runs >= 100,
        "no_external_device_access": True,
        "no_control_output": True,
    }
    payload = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "S100 U1 temporal occupancy/flow single-board offline CPU/BPU A/B",
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "runtime": {
            "onnxruntime": ort.__version__,
            "providers": ort.get_available_providers(),
            "hbm_runtime": str(HB_HBMRuntime.version),
            "hbm_model_names": list(runtime.model_names),
            "hbm_input_names": runtime.input_names,
            "hbm_input_shapes": runtime.input_shapes,
            "hbm_output_names": runtime.output_names,
            "hbm_output_shapes": runtime.output_shapes,
        },
        "deployment_manifest_sha256": sha256(manifest_path),
        "test_count": len(tests),
        "acceptance": {
            "mean_cosine_target": MEAN_COSINE_TARGET,
            "per_sample_output_cosine_floor": PER_SAMPLE_COSINE_FLOOR,
            "warmups_min": 20,
            "measurements_min": 100,
            "latency": "recorded only; no arbitrary U1 latency gate",
        },
        "checks": checks,
        "cosine_global_mean": float(np.mean(all_similarities)),
        "cosine_global_min": min(all_similarities),
        "outputs": output_metrics,
        "s100_cpu_timing": cpu_timing,
        "s100_bpu_runtime_api_timing": bpu_timing,
        "external_device_access": False,
        "control_output": False,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    print(
        json.dumps(
            {
                "result": payload["result"],
                "cosine_mean": payload["cosine_global_mean"],
                "cosine_min": payload["cosine_global_min"],
                "bpu_p99_ms": bpu_timing["p99_ms"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
