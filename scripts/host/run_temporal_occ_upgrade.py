#!/usr/bin/env python3
"""Train, A/B evaluate, and export the tiny temporal BEV candidate.

All data are deterministic synthetic arrays.  The script does not import ROS,
open a device, or publish a command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn.functional as functional
from torch.utils.data import DataLoader, TensorDataset

if hasattr(ort, "preload_dlls"):
    ort.preload_dlls()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_everything(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def as_dataset(batch: object) -> TensorDataset:
    return TensorDataset(
        torch.from_numpy(batch.history),
        torch.from_numpy(batch.future),
        torch.from_numpy(batch.flow),
        torch.from_numpy(batch.dynamic_mask),
        torch.from_numpy(batch.uncertainty),
    )


def training_loss(
    outputs: tuple[torch.Tensor, torch.Tensor, torch.Tensor],
    targets: tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor],
) -> tuple[torch.Tensor, dict[str, float]]:
    occupancy_logits, flow_prediction, uncertainty_logits = outputs
    future, flow, dynamic_mask, uncertainty = targets
    occupancy_weight = 1.0 + 5.0 * future
    occupancy_bce = (functional.binary_cross_entropy_with_logits(occupancy_logits, future, reduction="none") * occupancy_weight).mean()
    probability = torch.sigmoid(occupancy_logits)
    numerator = 2.0 * (probability * future).sum(dim=(1, 2, 3)) + 1.0
    denominator = probability.sum(dim=(1, 2, 3)) + future.sum(dim=(1, 2, 3)) + 1.0
    dice = (1.0 - numerator / denominator).mean()
    expanded_mask = dynamic_mask.expand_as(flow_prediction)
    flow_error = functional.smooth_l1_loss(flow_prediction, flow, reduction="none")
    flow_loss = (flow_error * expanded_mask).sum() / expanded_mask.sum().clamp_min(1.0)
    uncertainty_weight = 1.0 + 2.0 * uncertainty
    uncertainty_loss = (
        functional.binary_cross_entropy_with_logits(uncertainty_logits, uncertainty, reduction="none")
        * uncertainty_weight
    ).mean()
    total = occupancy_bce + 0.8 * dice + 0.7 * flow_loss + 0.15 * uncertainty_loss
    return total, {
        "total": float(total.detach()),
        "occupancy_bce": float(occupancy_bce.detach()),
        "dice": float(dice.detach()),
        "flow": float(flow_loss.detach()),
        "uncertainty": float(uncertainty_loss.detach()),
    }


def binary_iou(prediction: np.ndarray, target: np.ndarray) -> float:
    prediction_bool = prediction.astype(bool)
    target_bool = target.astype(bool)
    intersection = np.logical_and(prediction_bool, target_bool).sum(axis=(1, 2, 3))
    union = np.logical_or(prediction_bool, target_bool).sum(axis=(1, 2, 3))
    return float(np.mean((intersection + 1.0e-6) / (union + 1.0e-6)))


def evaluate(model: torch.nn.Module, batch: object, device: torch.device, batch_size: int) -> dict:
    model.eval()
    occupancy_parts: list[np.ndarray] = []
    flow_parts: list[np.ndarray] = []
    uncertainty_parts: list[np.ndarray] = []
    loader = DataLoader(as_dataset(batch), batch_size=batch_size, shuffle=False)
    with torch.inference_mode():
        for history, _future, _flow, _mask, _uncertainty in loader:
            outputs = model(history.to(device))
            occupancy_parts.append(torch.sigmoid(outputs[0]).cpu().numpy())
            flow_parts.append(outputs[1].cpu().numpy())
            uncertainty_parts.append(torch.sigmoid(outputs[2]).cpu().numpy())
    occupancy_probability = np.concatenate(occupancy_parts)
    flow_prediction = np.concatenate(flow_parts)
    uncertainty_probability = np.concatenate(uncertainty_parts)
    candidate_binary = occupancy_probability >= 0.5
    persistence = np.repeat((batch.history[:, -1:] >= 0.5), batch.future.shape[1], axis=1)
    dynamic = batch.dynamic_mask.astype(bool)
    candidate_flow_error = np.sqrt(np.sum((flow_prediction - batch.flow) ** 2, axis=1, keepdims=True))
    baseline_flow_error = np.sqrt(np.sum(batch.flow**2, axis=1, keepdims=True))
    candidate_epe = float(candidate_flow_error[dynamic].mean())
    baseline_epe = float(baseline_flow_error[dynamic].mean())
    absolute_error = np.abs(occupancy_probability - batch.future)
    sample_error = absolute_error.mean(axis=(1, 2, 3))
    change_region = batch.uncertainty.astype(bool)
    stable_region = np.logical_not(change_region)
    uncertainty_change = float(uncertainty_probability[change_region].mean())
    uncertainty_stable = float(uncertainty_probability[stable_region].mean())
    return {
        "candidate_mean_iou": binary_iou(candidate_binary, batch.future),
        "persistence_mean_iou": binary_iou(persistence, batch.future),
        "candidate_dynamic_flow_epe": candidate_epe,
        "zero_flow_dynamic_epe": baseline_epe,
        "mean_absolute_probability_error": float(absolute_error.mean()),
        "sample_error": sample_error,
        "uncertainty_change_mean": uncertainty_change,
        "uncertainty_stable_mean": uncertainty_stable,
        "history": batch.history,
        "future": batch.future,
        "torch_outputs": [occupancy_probability, flow_prediction, uncertainty_probability],
    }


def run_onnx(model_path: Path, provider: str, sample: np.ndarray, repeats: int = 50) -> dict:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    # ORT enables TF32 by default on Ampere/Ada.  Disable it for this parity
    # gate so the exported model is compared under IEEE float32 semantics; the
    # faster default remains a separately measurable deployment option later.
    provider_spec: list[object]
    if provider == "CUDAExecutionProvider":
        provider_spec = [(provider, {"use_tf32": "0"}), "CPUExecutionProvider"]
    else:
        provider_spec = [provider]
    session = ort.InferenceSession(str(model_path), options, providers=provider_spec)
    feed = {session.get_inputs()[0].name: sample}
    for _ in range(5):
        session.run(None, feed)
    timings: list[float] = []
    outputs = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        outputs = session.run(None, feed)
        timings.append((time.perf_counter_ns() - start) / 1_000_000.0)
    return {
        "provider": provider,
        "active_providers": session.get_providers(),
        "provider_options": session.get_provider_options().get(provider, {}),
        "finite": all(bool(np.isfinite(output).all()) for output in outputs),
        "latency_ms": {
            "repeats": repeats,
            "min": min(timings),
            "median": statistics.median(timings),
            "mean": statistics.fmean(timings),
            "max": max(timings),
        },
        "output_shapes": [list(output.shape) for output in outputs],
        "outputs": outputs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--epochs", type=int, default=24)
    parser.add_argument("--train-samples", type=int, default=4096)
    parser.add_argument("--calibration-samples", type=int, default=512)
    parser.add_argument("--test-samples", type=int, default=768)
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args()

    repo = args.repo.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo / "src"))
    from roboto_upgrade.synthetic_bev import generate_dataset  # pylint: disable=import-outside-toplevel
    from roboto_upgrade.temporal_occ_flow import TinyTemporalOccFlow  # pylint: disable=import-outside-toplevel
    from roboto_upgrade.trust_guard import split_conformal_threshold  # pylint: disable=import-outside-toplevel

    seed_everything(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train = generate_dataset(args.train_samples, seed=args.seed)
    calibration = generate_dataset(args.calibration_samples, seed=args.seed + 1)
    test = generate_dataset(args.test_samples, seed=args.seed + 2)
    model = TinyTemporalOccFlow().to(device)
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    optimizer = torch.optim.AdamW(model.parameters(), lr=2.0e-3, weight_decay=1.0e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs, eta_min=2.0e-4)
    generator = torch.Generator().manual_seed(args.seed)
    loader = DataLoader(as_dataset(train), batch_size=args.batch_size, shuffle=True, generator=generator)
    history: list[dict] = []
    started = time.perf_counter()
    for epoch in range(args.epochs):
        model.train()
        totals: dict[str, list[float]] = {name: [] for name in ("total", "occupancy_bce", "dice", "flow", "uncertainty")}
        for input_history, future, flow, mask, uncertainty in loader:
            input_history = input_history.to(device)
            targets = (future.to(device), flow.to(device), mask.to(device), uncertainty.to(device))
            optimizer.zero_grad(set_to_none=True)
            loss, components = training_loss(model(input_history), targets)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            for name, value in components.items():
                totals[name].append(value)
        scheduler.step()
        record = {"epoch": epoch + 1, "learning_rate": optimizer.param_groups[0]["lr"]}
        record.update({name: statistics.fmean(values) for name, values in totals.items()})
        history.append(record)
        print(json.dumps(record))
    training_seconds = time.perf_counter() - started

    calibration_metrics = evaluate(model, calibration, device, args.batch_size)
    test_metrics = evaluate(model, test, device, args.batch_size)
    conformal_threshold = split_conformal_threshold(calibration_metrics["sample_error"], alpha=0.05)
    empirical_coverage = float(np.mean(test_metrics["sample_error"] <= conformal_threshold))

    checkpoint = output_dir / "tiny_temporal_occ_flow.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "seed": args.seed,
            "architecture": {"history_frames": 4, "future_frames": 3, "channels": 24},
            "parameter_count": parameter_count,
            "conformal_error_threshold": conformal_threshold,
        },
        checkpoint,
    )
    model_cpu = model.cpu().eval()
    onnx_path = output_dir / "tiny_temporal_occ_flow.onnx"
    export_input = torch.from_numpy(test.history[:1])
    torch.onnx.export(
        model_cpu,
        export_input,
        onnx_path,
        input_names=["bev_history"],
        output_names=["occupancy_logits", "flow", "uncertainty_logits"],
        opset_version=17,
        do_constant_folding=True,
    )
    onnx_model = onnx.load(str(onnx_path), load_external_data=True)
    onnx.checker.check_model(onnx_model)

    with torch.inference_mode():
        raw_torch_outputs = [value.numpy() for value in model_cpu(export_input)]
    providers = [provider for provider in ("CPUExecutionProvider", "CUDAExecutionProvider") if provider in ort.get_available_providers()]
    backend_results: dict[str, dict] = {}
    backend_outputs: dict[str, list[np.ndarray]] = {}
    for provider in providers:
        result = run_onnx(onnx_path, provider, test.history[:1])
        backend_outputs[provider] = result.pop("outputs")
        backend_results[provider] = result
        result["vs_torch"] = [
            {
                "max_abs_error": float(np.max(np.abs(actual - expected))),
                "allclose_rtol_1e-4_atol_1e-5": bool(np.allclose(actual, expected, rtol=1.0e-4, atol=1.0e-5)),
            }
            for actual, expected in zip(backend_outputs[provider], raw_torch_outputs)
        ]

    vector_path = output_dir / "x5_probe_input.npz"
    np.savez(
        vector_path,
        bev_history=test.history[:1],
        occupancy_logits=raw_torch_outputs[0],
        flow=raw_torch_outputs[1],
        uncertainty_logits=raw_torch_outputs[2],
    )

    compact_test_metrics = {key: value for key, value in test_metrics.items() if key not in {"sample_error", "history", "future", "torch_outputs"}}
    criteria = {
        "occupancy_beats_persistence": compact_test_metrics["candidate_mean_iou"] >= compact_test_metrics["persistence_mean_iou"] + 0.05,
        "flow_beats_zero": compact_test_metrics["candidate_dynamic_flow_epe"] <= compact_test_metrics["zero_flow_dynamic_epe"] * 0.75,
        "uncertainty_separates_change": compact_test_metrics["uncertainty_change_mean"] > compact_test_metrics["uncertainty_stable_mean"],
        "conformal_coverage_gte_90pct": empirical_coverage >= 0.90,
        "onnx_checker": True,
        "onnx_all_backends_finite": all(result["finite"] for result in backend_results.values()),
        "onnx_matches_torch": all(
            comparison["allclose_rtol_1e-4_atol_1e-5"]
            for result in backend_results.values()
            for comparison in result["vs_torch"]
        ),
        "cuda_backend_exercised": "CUDAExecutionProvider" in backend_results,
    }
    passed = all(criteria.values())
    source_paths = [
        repo / "src/roboto_upgrade/synthetic_bev.py",
        repo / "src/roboto_upgrade/temporal_occ_flow.py",
        repo / "scripts/host/run_temporal_occ_upgrade.py",
    ]
    payload = {
        "schema_version": 1,
        "scope": "synthetic BEV training/export only; no ROS or devices",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "device": str(device),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "seed": args.seed,
        "dataset": {
            "train": args.train_samples,
            "calibration": args.calibration_samples,
            "test": args.test_samples,
            "input_shape": [1, 4, 32, 32],
            "future_horizons": 3,
        },
        "model": {"parameter_count": parameter_count, "checkpoint_sha256": sha256(checkpoint), "onnx_sha256": sha256(onnx_path)},
        "training": {"epochs": args.epochs, "seconds": training_seconds, "history": history},
        "ab": compact_test_metrics,
        "calibration": {
            "alpha": 0.05,
            "threshold": conformal_threshold,
            "test_empirical_coverage": empirical_coverage,
        },
        "onnx_backends": backend_results,
        "x5_probe_vector": {"path": vector_path.name, "sha256": sha256(vector_path)},
        "sources": [{"path": path.relative_to(repo).as_posix(), "sha256": sha256(path)} for path in source_paths],
        "criteria": criteria,
        "result": "PASS" if passed else "FAIL",
    }
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "output": str(result_path), "ab": compact_test_metrics, "criteria": criteria}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
