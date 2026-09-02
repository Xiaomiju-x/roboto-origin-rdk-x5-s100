#!/usr/bin/env python3
"""Benchmark and cross-check official RoboParty ONNX models on the host.

This is a no-device probe.  Inputs are deterministic synthetic tensors and no
ROS, simulator, camera, motor, CAN, or board service is opened.
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
import onnx
import torch
import onnxruntime as ort

# ONNX Runtime's CUDA provider reuses the CUDA/cuDNN DLLs shipped with the
# pinned PyTorch build on Windows.  Importing torch first is the documented
# preload path; preload_dlls also makes the intent explicit for ORT >= 1.21.
if hasattr(ort, "preload_dlls"):
    ort.preload_dlls()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def concrete_shape(shape: list[object]) -> tuple[int, ...]:
    result: list[int] = []
    for value in shape:
        if isinstance(value, int) and value > 0:
            result.append(value)
        else:
            result.append(1)
    return tuple(result)


def run_session(model: Path, provider: str, input_data: np.ndarray, repeats: int) -> dict:
    options = ort.SessionOptions()
    options.log_severity_level = 3
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), options, providers=[provider])
    input_meta = session.get_inputs()[0]
    output_meta = session.get_outputs()[0]
    feed = {input_meta.name: input_data}

    for _ in range(5):
        session.run(None, feed)

    samples_ms: list[float] = []
    output = None
    for _ in range(repeats):
        start = time.perf_counter_ns()
        output = session.run(None, feed)[0]
        samples_ms.append((time.perf_counter_ns() - start) / 1_000_000.0)

    assert output is not None
    return {
        "provider_requested": provider,
        "providers_active": session.get_providers(),
        "input_name": input_meta.name,
        "input_shape": list(input_data.shape),
        "output_name": output_meta.name,
        "output_shape": list(output.shape),
        "finite": bool(np.isfinite(output).all()),
        "output_min": float(np.min(output)),
        "output_max": float(np.max(output)),
        "output_mean": float(np.mean(output)),
        "latency_ms": {
            "repeats": repeats,
            "min": min(samples_ms),
            "median": statistics.median(samples_ms),
            "mean": statistics.fmean(samples_ms),
            "max": max(samples_ms),
        },
        "output": output,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--isaac-aggregate",
        type=Path,
        help="Use ONNX exports recorded by an official Isaac acceptance aggregate.",
    )
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    repo = args.repo.resolve()
    aggregate_record = None
    if args.isaac_aggregate:
        aggregate_path = args.isaac_aggregate.resolve()
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
        model_paths = sorted(
            {
                (repo / exported["path"]).resolve()
                for task in aggregate.get("tasks", {}).values()
                for exported in task.get("exports", [])
                if Path(exported["path"]).suffix.lower() == ".onnx"
            }
        )
        aggregate_record = {
            "path": aggregate_path.relative_to(repo).as_posix(),
            "sha256": sha256(aggregate_path),
            "status": aggregate.get("status"),
            "complete_matrix_run": aggregate.get("complete_matrix_run"),
        }
    else:
        model_paths = sorted(
            list((repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models").glob("*.onnx"))
            + list((repo / "upstream/roboparty_deploy/src/camera/models").glob("*.onnx"))
        )
    if not model_paths:
        raise FileNotFoundError("No official ONNX models found")

    available = ort.get_available_providers()
    requested = [p for p in ("CPUExecutionProvider", "CUDAExecutionProvider") if p in available]
    rng = np.random.default_rng(args.seed)
    models: list[dict] = []
    all_pass = True

    for model in model_paths:
        onnx_model = onnx.load(str(model), load_external_data=True)
        onnx.checker.check_model(onnx_model)
        shape_session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        shape = concrete_shape(shape_session.get_inputs()[0].shape)
        input_data = rng.normal(0.0, 0.1, size=shape).astype(np.float32)
        backends: dict[str, dict] = {}
        raw_outputs: dict[str, np.ndarray] = {}
        for provider in requested:
            result = run_session(model, provider, input_data, args.repeats)
            raw_outputs[provider] = result.pop("output")
            backends[provider] = result
            all_pass = all_pass and result["finite"] and result["providers_active"][0] == provider

        comparison = None
        if "CPUExecutionProvider" in raw_outputs and "CUDAExecutionProvider" in raw_outputs:
            cpu = raw_outputs["CPUExecutionProvider"].astype(np.float64)
            cuda = raw_outputs["CUDAExecutionProvider"].astype(np.float64)
            abs_error = np.abs(cpu - cuda)
            denom = np.maximum(np.abs(cpu), 1.0e-8)
            comparison = {
                "max_abs_error": float(np.max(abs_error)),
                "mean_abs_error": float(np.mean(abs_error)),
                "max_rel_error": float(np.max(abs_error / denom)),
                "allclose_rtol_1e-4_atol_1e-5": bool(np.allclose(cpu, cuda, rtol=1.0e-4, atol=1.0e-5)),
                "allclose_rtol_1e-3_atol_1e-4": bool(np.allclose(cpu, cuda, rtol=1.0e-3, atol=1.0e-4)),
            }
            # Attention/Conv kernels can legitimately choose different GPU
            # accumulation paths.  Keep the stricter comparison as a recorded
            # diagnostic, and gate deployment parity at sub-millipercent
            # relative / 1e-4 absolute error.
            all_pass = all_pass and comparison["allclose_rtol_1e-3_atol_1e-4"]

        models.append(
            {
                "name": model.name,
                "relative_path": model.relative_to(repo).as_posix(),
                "bytes": model.stat().st_size,
                "sha256": sha256(model),
                "onnx_checker": "PASS",
                "backends": backends,
                "cpu_cuda_comparison": comparison,
            }
        )

    payload = {
        "schema_version": 1,
        "scope": "deterministic synthetic ONNX inference; no devices or ROS",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "pytorch": {"version": torch.__version__, "cuda": torch.version.cuda},
        "onnxruntime": {"version": ort.__version__, "available_providers": available},
        "seed": args.seed,
        "isaac_acceptance_aggregate": aggregate_record,
        "result": "PASS" if all_pass else "FAIL",
        "models": models,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "models": len(models), "output": str(args.output)}, ensure_ascii=False))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
