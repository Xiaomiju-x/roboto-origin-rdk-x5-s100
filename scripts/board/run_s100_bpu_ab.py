#!/usr/bin/env python3
"""Compare host float, S100 CPU ONNX, and S100 Nash-e HBM offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort
from hbm_runtime import HB_HBMRuntime


CPU_PORTABILITY_ATOL = 1e-5
CPU_PORTABILITY_RTOL = 1e-5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: list[float], q: float) -> float:
    ordered = np.asarray(values, dtype=np.float64)
    return float(np.percentile(ordered, q, method="linear"))


def cosine(reference: np.ndarray, observed: np.ndarray) -> float:
    lhs = reference.astype(np.float64, copy=False).reshape(-1)
    rhs = observed.astype(np.float64, copy=False).reshape(-1)
    denominator = float(np.linalg.norm(lhs) * np.linalg.norm(rhs))
    if denominator <= 1e-12:
        return 1.0 if np.allclose(lhs, rhs, atol=1e-7, rtol=1e-7) else 0.0
    return float(np.dot(lhs, rhs) / denominator)


def only_output(result: dict[str, dict[str, np.ndarray]]) -> np.ndarray:
    if len(result) != 1:
        raise RuntimeError(f"expected one model result, got {list(result)}")
    outputs = next(iter(result.values()))
    if len(outputs) != 1:
        raise RuntimeError(f"expected one output, got {list(outputs)}")
    return np.asarray(next(iter(outputs.values())))


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=20)
    parser.add_argument("--runs", type=int, default=200)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conversion_path = bundle / "logs" / "conversion_summary.json"
    conversion = json.loads(conversion_path.read_text(encoding="utf-8"))
    compiled_lock = {
        item["name"]: item for item in conversion["models"] if item["status"] == "PASS"
    }
    acceptance = manifest["acceptance"]
    results: list[dict[str, object]] = []

    for spec in manifest["models"]:
        name = spec["name"]
        deployment = spec.get("deployment", "BPU")
        if deployment not in {"BPU", "CPU_FALLBACK"}:
            raise RuntimeError(f"unsupported deployment decision for {name}: {deployment}")
        onnx_path = bundle / "models" / f"{name}.onnx"
        test_path = bundle / "test" / name / "inputs.npy"
        host_reference_path = bundle / "host_reference" / f"{name}.npy"
        if sha256(onnx_path) != spec["onnx_sha256"]:
            raise RuntimeError(f"ONNX hash mismatch: {name}")
        if sha256(test_path) != spec["test_input_sha256"]:
            raise RuntimeError(f"test fixture hash mismatch: {name}")
        if sha256(host_reference_path) != spec["host_reference_sha256"]:
            raise RuntimeError(f"host reference hash mismatch: {name}")
        compile_model_path = bundle / spec["compile_onnx_file"]
        if sha256(compile_model_path) != spec["compile_onnx_sha256"]:
            raise RuntimeError(f"compile ONNX hash mismatch: {name}")

        tests = np.load(test_path, allow_pickle=False).astype(np.float32, copy=False)
        host_reference = np.load(host_reference_path, allow_pickle=False).astype(
            np.float32, copy=False
        )
        cpu_session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        cpu_input = cpu_session.get_inputs()[0].name
        cpu_output = cpu_session.get_outputs()[0].name
        cpu_results = np.concatenate(
            [cpu_session.run([cpu_output], {cpu_input: sample[None]})[0] for sample in tests],
            axis=0,
        )
        host_cpu_max_abs = float(np.max(np.abs(cpu_results - host_reference)))
        host_cpu_scale = float(np.max(np.abs(host_reference)))
        host_cpu_tolerance = CPU_PORTABILITY_ATOL + CPU_PORTABILITY_RTOL * host_cpu_scale
        host_cpu_allclose = bool(
            np.allclose(
                cpu_results,
                host_reference,
                atol=CPU_PORTABILITY_ATOL,
                rtol=CPU_PORTABILITY_RTOL,
            )
        )

        benchmark_sample = np.ascontiguousarray(tests[min(5, len(tests) - 1)][None])
        cpu_timing = benchmark(
            lambda: cpu_session.run([cpu_output], {cpu_input: benchmark_sample}),
            args.warmups,
            args.runs,
        )
        is_policy = name.startswith("policy")
        if deployment == "CPU_FALLBACK":
            cpu_checks = {
                "finite": bool(np.isfinite(cpu_results).all()),
                "host_vs_s100_cpu_allclose_atol_1e_5_rtol_1e_5": host_cpu_allclose,
                "policy_cpu_p99_latency": (not is_policy)
                or float(cpu_timing["p99_ms"])
                <= float(acceptance["policy_p99_ms_max"]),
                "warmup_and_measurement_counts": args.warmups >= 20
                and args.runs >= 100,
            }
            results.append(
                {
                    "name": name,
                    "status": "CPU_FALLBACK" if all(cpu_checks.values()) else "FAIL",
                    "deployment": "CPUExecutionProvider",
                    "reason": spec.get(
                        "deployment_reason",
                        "explicit CPU fallback selected in the frozen manifest",
                    ),
                    "checks": cpu_checks,
                    "onnx_sha256": sha256(onnx_path),
                    "test_count": len(tests),
                    "host_vs_s100_cpu_max_abs": host_cpu_max_abs,
                    "host_vs_s100_cpu_reference_abs_max": host_cpu_scale,
                    "host_vs_s100_cpu_global_scale_tolerance": host_cpu_tolerance,
                    "host_vs_s100_cpu_atol": CPU_PORTABILITY_ATOL,
                    "host_vs_s100_cpu_rtol": CPU_PORTABILITY_RTOL,
                    "s100_cpu_timing": cpu_timing,
                    "rejected_bpu_candidates": spec.get("rejected_bpu_candidates", []),
                }
            )
            continue

        hbm_candidates = sorted((bundle / "compiled" / name).glob("*.hbm"))
        if len(hbm_candidates) != 1:
            results.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "deployment": "BPU",
                    "reason": f"expected one HBM, found {len(hbm_candidates)}",
                    "onnx_sha256": spec["onnx_sha256"],
                    "s100_cpu_timing": cpu_timing,
                }
            )
            continue

        hbm_path = hbm_candidates[0]
        locked_artifacts = compiled_lock.get(name, {}).get("artifacts", [])
        if len(locked_artifacts) != 1 or sha256(hbm_path) != locked_artifacts[0]["sha256"]:
            raise RuntimeError(f"compiled HBM hash mismatch: {name}")
        runtime = HB_HBMRuntime(str(hbm_path))
        bpu_results = np.concatenate(
            [only_output(runtime.run(np.ascontiguousarray(sample[None]))).reshape(1, -1) for sample in tests],
            axis=0,
        ).astype(np.float32, copy=False)
        if bpu_results.shape != cpu_results.shape:
            raise RuntimeError(
                f"output shape mismatch for {name}: {bpu_results.shape} != {cpu_results.shape}"
            )

        similarities = [
            cosine(cpu_results[index], bpu_results[index]) for index in range(len(tests))
        ]
        max_abs_error = float(np.max(np.abs(cpu_results - bpu_results)))
        finite = bool(np.isfinite(cpu_results).all() and np.isfinite(bpu_results).all())
        direction_mask = np.abs(cpu_results) >= float(acceptance["policy_direction_threshold"])
        direction_count = int(np.count_nonzero(direction_mask))
        direction_agreement = (
            float(
                np.mean(
                    np.signbit(cpu_results[direction_mask])
                    == np.signbit(bpu_results[direction_mask])
                )
            )
            if direction_count
            else 1.0
        )

        bpu_timing = benchmark(lambda: runtime.run(benchmark_sample), args.warmups, args.runs)
        checks = {
            "finite": finite,
            "host_vs_s100_cpu_allclose_atol_1e_5_rtol_1e_5": host_cpu_allclose,
            "mean_cosine_target": float(np.mean(similarities))
            >= float(acceptance["cosine_similarity_target"]),
            "per_sample_cosine_floor": min(similarities)
            >= float(acceptance["per_sample_cosine_hard_floor"]),
            "policy_max_abs_error": (not is_policy)
            or max_abs_error <= float(acceptance["policy_max_abs_error"]),
            "policy_direction_agreement": (not is_policy)
            or direction_agreement >= float(acceptance["policy_direction_agreement_min"]),
            "policy_p99_latency": (not is_policy)
            or float(bpu_timing["p99_ms"]) <= float(acceptance["policy_p99_ms_max"]),
            "warmup_and_measurement_counts": args.warmups >= 20 and args.runs >= 100,
        }
        results.append(
            {
                "name": name,
                "status": "PASS" if all(checks.values()) else "FAIL",
                "deployment": "BPU",
                "checks": checks,
                "onnx_sha256": sha256(onnx_path),
                "hbm_sha256": sha256(hbm_path),
                "hbm_file": str(hbm_path.relative_to(bundle)),
                "runtime_model_names": list(runtime.model_names),
                "runtime_input_names": runtime.input_names,
                "runtime_input_shapes": runtime.input_shapes,
                "runtime_output_names": runtime.output_names,
                "runtime_output_shapes": runtime.output_shapes,
                "test_count": len(tests),
                "host_vs_s100_cpu_max_abs": host_cpu_max_abs,
                "host_vs_s100_cpu_reference_abs_max": host_cpu_scale,
                "host_vs_s100_cpu_global_scale_tolerance": host_cpu_tolerance,
                "host_vs_s100_cpu_atol": CPU_PORTABILITY_ATOL,
                "host_vs_s100_cpu_rtol": CPU_PORTABILITY_RTOL,
                "bpu_cosine_mean": float(np.mean(similarities)),
                "bpu_cosine_min": min(similarities),
                "bpu_cosine_max": max(similarities),
                "bpu_max_abs_error": max_abs_error,
                "policy_direction_count": direction_count,
                "policy_direction_agreement": direction_agreement,
                "s100_cpu_timing": cpu_timing,
                "s100_bpu_runtime_api_timing": bpu_timing,
            }
        )

    pass_count = sum(item["status"] == "PASS" for item in results)
    fallback_count = sum(item["status"] == "CPU_FALLBACK" for item in results)
    fail_count = sum(item["status"] == "FAIL" for item in results)
    required_bpu = {"encoder", "policy"}
    passed_names = {item["name"] for item in results if item["status"] == "PASS"}
    overall_pass = fail_count == 0 and required_bpu.issubset(passed_names)
    output = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "S100 single-board offline CPU/BPU A/B; no robot or external peripherals",
        "host": platform.node(),
        "runtime": {
            "onnxruntime": ort.__version__,
            "hbm_runtime": str(HB_HBMRuntime.version),
            "providers": ort.get_available_providers(),
        },
        "source_manifest_sha256": sha256(manifest_path),
        "conversion_summary_sha256": sha256(conversion_path),
        "acceptance": acceptance,
        "summary": {
            "total": len(results),
            "pass": pass_count,
            "cpu_fallback": fallback_count,
            "fail": fail_count,
            "required_bpu_models": sorted(required_bpu),
            "required_bpu_models_pass": required_bpu.issubset(passed_names),
        },
        "models": results,
        "external_device_access": False,
        "control_output": False,
        "bpu_runtime_access": True,
        "result": "PASS" if overall_pass else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    print(json.dumps(output["summary"], ensure_ascii=False))
    if not overall_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
