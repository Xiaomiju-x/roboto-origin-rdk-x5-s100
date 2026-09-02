#!/usr/bin/env python3
"""Freeze the S100 attention-policy BPU precision sweep and fallback decision."""

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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cosine(lhs: np.ndarray, rhs: np.ndarray) -> float:
    a = lhs.astype(np.float64, copy=False).reshape(-1)
    b = rhs.astype(np.float64, copy=False).reshape(-1)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return 1.0 if np.allclose(a, b, atol=1e-7, rtol=1e-7) else 0.0
    return float(np.dot(a, b) / denominator)


def only_output(result: dict[str, dict[str, np.ndarray]]) -> np.ndarray:
    return np.asarray(next(iter(next(iter(result.values())).values())))


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        help="label=/absolute/or/bundle-relative/model.hbm",
    )
    parser.add_argument("--warmups", type=int, default=20)
    parser.add_argument("--runs", type=int, default=200)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    acceptance = manifest["acceptance"]
    model_name = "policy_attn_enc"
    model_path = bundle / "models" / f"{model_name}.onnx"
    test_path = bundle / "test" / model_name / "inputs.npy"
    tests = np.load(test_path, allow_pickle=False).astype(np.float32, copy=False)

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    cpu_results = np.concatenate(
        [session.run([output_name], {input_name: sample[None]})[0] for sample in tests],
        axis=0,
    ).astype(np.float32, copy=False)
    benchmark_sample = np.ascontiguousarray(tests[min(5, len(tests) - 1)][None])
    cpu_timing = benchmark(
        lambda: session.run([output_name], {input_name: benchmark_sample}),
        args.warmups,
        args.runs,
    )

    candidates = []
    for item in args.candidate:
        if "=" not in item:
            raise RuntimeError(f"candidate must be label=path: {item}")
        label, raw_path = item.split("=", 1)
        hbm_path = Path(raw_path)
        if not hbm_path.is_absolute():
            hbm_path = bundle / hbm_path
        hbm_path = hbm_path.resolve()
        runtime = HB_HBMRuntime(str(hbm_path))
        bpu_results = np.concatenate(
            [
                only_output(runtime.run(np.ascontiguousarray(sample[None]))).reshape(1, -1)
                for sample in tests
            ],
            axis=0,
        ).astype(np.float32, copy=False)
        similarities = [
            cosine(cpu_results[index], bpu_results[index])
            for index in range(len(tests))
        ]
        mask = np.abs(cpu_results) >= float(acceptance["policy_direction_threshold"])
        direction_agreement = float(
            np.mean(np.signbit(cpu_results[mask]) == np.signbit(bpu_results[mask]))
        )
        timing = benchmark(
            lambda: runtime.run(benchmark_sample), args.warmups, args.runs
        )
        checks = {
            "finite": bool(np.isfinite(bpu_results).all()),
            "mean_cosine_target": float(np.mean(similarities))
            >= float(acceptance["cosine_similarity_target"]),
            "per_sample_cosine_floor": min(similarities)
            >= float(acceptance["per_sample_cosine_hard_floor"]),
            "policy_max_abs_error": float(np.max(np.abs(cpu_results - bpu_results)))
            <= float(acceptance["policy_max_abs_error"]),
            "policy_direction_agreement": direction_agreement
            >= float(acceptance["policy_direction_agreement_min"]),
            "policy_p99_latency": float(timing["p99_ms"])
            <= float(acceptance["policy_p99_ms_max"]),
            "warmup_and_measurement_counts": args.warmups >= 20 and args.runs >= 100,
        }
        candidates.append(
            {
                "label": label,
                "path": str(hbm_path),
                "sha256": sha256(hbm_path),
                "bytes": hbm_path.stat().st_size,
                "status": "PASS" if all(checks.values()) else "REJECTED",
                "checks": checks,
                "cosine_mean": float(np.mean(similarities)),
                "cosine_min": min(similarities),
                "max_abs_error": float(np.max(np.abs(cpu_results - bpu_results))),
                "direction_count": int(np.count_nonzero(mask)),
                "direction_agreement": direction_agreement,
                "bpu_runtime_api_timing": timing,
            }
        )

    passing = [item for item in candidates if item["status"] == "PASS"]
    cpu_checks = {
        "finite": bool(np.isfinite(cpu_results).all()),
        "policy_cpu_p99_latency": float(cpu_timing["p99_ms"])
        <= float(acceptance["policy_p99_ms_max"]),
        "warmup_and_measurement_counts": args.warmups >= 20 and args.runs >= 100,
    }
    if passing:
        selected = min(passing, key=lambda item: item["bpu_runtime_api_timing"]["p99_ms"])
        decision = {
            "deployment": "BPU",
            "selected_candidate": selected["label"],
            "reason": "At least one frozen HBM candidate passed every numerical and latency gate.",
        }
    else:
        decision = {
            "deployment": "CPU_FALLBACK",
            "selected_candidate": None,
            "reason": "No frozen HBM candidate passed every numerical and latency gate; CPU passed the same policy p99 gate.",
        }

    result = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "S100 policy_attn_enc offline precision sweep; no robot or external peripherals",
        "host": platform.node(),
        "model": model_name,
        "model_sha256": sha256(model_path),
        "test_input_sha256": sha256(test_path),
        "test_count": len(tests),
        "runtime": {
            "onnxruntime": ort.__version__,
            "hbm_runtime": str(HB_HBMRuntime.version),
        },
        "acceptance": acceptance,
        "cpu": {"checks": cpu_checks, "timing": cpu_timing},
        "candidates": candidates,
        "decision": decision,
        "external_device_access": False,
        "control_output": False,
        "result": "PASS" if all(cpu_checks.values()) else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    print(json.dumps(decision, ensure_ascii=False))
    if result["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
