#!/usr/bin/env python3
"""Run bounded no-device probes through RoboParty's official MuJoCo loops."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MPLBACKEND", "Agg")

import mujoco
import numpy as np
import onnxruntime as ort
import torch

if hasattr(ort, "preload_dlls"):
    ort.preload_dlls()


CASES = {
    "flat": ("sim2sim_rpo.py", "policy.onnx", False, None),
    "rough": ("sim2sim_rpo.py", "policy.onnx", True, None),
    "amp": ("sim2sim_rpo_amp.py", "policy_amp.onnx", True, None),
    "attn_enc": ("sim2sim_rpo_attn_enc.py", "policy_attn_enc.onnx", True, None),
    "interrupt": ("sim2sim_rpo_interrupt.py", "policy_interrupt.onnx", False, None),
    "wave": ("sim2sim_rpo_bm.py", "policy_wave.onnx", False, "yundong0.npz"),
    "dance0": ("sim2sim_rpo_bm.py", "policy_dance0.onnx", False, "yundong0.npz"),
    "dance1": ("sim2sim_rpo_bm.py", "policy_dance1.onnx", False, "yundong1.npz"),
    "getup": ("sim2sim_rpo_bm.py", "policy_getup.onnx", False, "getup_supin2prone.npz"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NullListener:
    def stop(self) -> None:
        return None


class NullRateLimiter:
    def __init__(self, *args, **kwargs):
        pass

    def sleep(self) -> None:
        return None


class OnnxTorchPolicy:
    def __init__(self, model: Path, provider: str):
        self.session = ort.InferenceSession(str(model), providers=[provider])
        if self.session.get_providers()[0] != provider:
            raise RuntimeError(f"Requested provider {provider} was not activated")
        self.input_name = self.session.get_inputs()[0].name
        self.calls = 0
        self.input_shapes: set[tuple[int, ...]] = set()
        self.all_finite = True
        self.max_abs_output = 0.0

    def __call__(self, tensor: torch.Tensor) -> torch.Tensor:
        array = tensor.detach().cpu().numpy().astype(np.float32, copy=False)
        self.input_shapes.add(tuple(array.shape))
        output = self.session.run(None, {self.input_name: array})[0]
        self.calls += 1
        self.all_finite = self.all_finite and bool(np.isfinite(output).all())
        if output.size:
            self.max_abs_output = max(self.max_abs_output, float(np.max(np.abs(output))))
        return torch.from_numpy(output)


def import_script(path: Path):
    spec = importlib.util.spec_from_file_location(f"roboparty_finite_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def extract_config(path: Path, module, terrain: bool):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "Sim2simCfg"]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one Sim2simCfg in {path}, found {len(candidates)}")
    class_node = candidates[0]
    compiled = compile(ast.fix_missing_locations(ast.Module(body=[class_node], type_ignores=[])), str(path), "exec")
    namespace = dict(module.__dict__)
    namespace["args"] = SimpleNamespace(terrain=terrain)
    exec(compiled, namespace)
    return namespace["Sim2simCfg"]()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=tuple(CASES), required=True)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--provider", choices=("CPUExecutionProvider", "CUDAExecutionProvider"), default="CPUExecutionProvider")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    script_name, model_name, terrain, motion_name = CASES[args.case]
    script_path = repo / "upstream/roboparty_train/robolab/scripts/mujoco" / script_name
    model_path = repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models" / model_name
    motion_path = repo / "upstream/roboparty_train/robolab/data/motions/rpo_bm" / motion_name if motion_name else None

    payload: dict = {
        "schema_version": 1,
        "scope": "finite official MuJoCo loop with official deployment ONNX; no robot or devices",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "result": "FAIL",
        "case": args.case,
        "requested_duration_s": args.duration,
        "provider": args.provider,
        "sources": {
            "official_script": {"path": script_path.relative_to(repo).as_posix(), "sha256": sha256(script_path)},
            "model": {"path": model_path.relative_to(repo).as_posix(), "sha256": sha256(model_path)},
        },
        "runtime": {"mujoco": mujoco.__version__, "onnxruntime": ort.__version__, "torch": torch.__version__},
    }
    if motion_path is not None:
        payload["sources"]["motion"] = {"path": motion_path.relative_to(repo).as_posix(), "sha256": sha256(motion_path)}

    original_cwd = Path.cwd()
    original_mj_step = mujoco.mj_step
    module = None
    stats = {
        "mj_step_calls": 0,
        "all_state_finite": True,
        "min_base_z": float("inf"),
        "max_base_z": float("-inf"),
        "max_abs_ctrl": 0.0,
    }
    try:
        module = import_script(script_path)
        cfg = extract_config(script_path, module, terrain)
        cfg.sim_config.sim_duration = args.duration
        policy = OnnxTorchPolicy(model_path, args.provider)

        if hasattr(module, "start_keyboard_listener"):
            module.start_keyboard_listener = lambda: NullListener()
        if hasattr(module, "print_controls_guide"):
            module.print_controls_guide = lambda: None
        if hasattr(module, "sleep_until"):
            module.sleep_until = lambda *unused_args, **unused_kwargs: None
        if hasattr(module, "RateLimiter"):
            module.RateLimiter = NullRateLimiter

        def observed_mj_step(model, data, *step_args, **step_kwargs):
            original_mj_step(model, data, *step_args, **step_kwargs)
            stats["mj_step_calls"] += 1
            stats["all_state_finite"] = stats["all_state_finite"] and bool(
                np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all() and np.isfinite(data.ctrl).all()
            )
            if data.qpos.size >= 3:
                stats["min_base_z"] = min(stats["min_base_z"], float(data.qpos[2]))
                stats["max_base_z"] = max(stats["max_base_z"], float(data.qpos[2]))
            if data.ctrl.size:
                stats["max_abs_ctrl"] = max(stats["max_abs_ctrl"], float(np.max(np.abs(data.ctrl))))

        module.mujoco.mj_step = observed_mj_step
        os.chdir(output_dir)
        start = time.perf_counter()
        if motion_path is None:
            module.run_mujoco(policy, cfg, True)
        else:
            module.run_mujoco(policy, cfg, True, False, str(motion_path))
        elapsed = time.perf_counter() - start

        artifact_names = ["simulation.mp4"]
        if args.case not in {"wave", "dance0", "dance1", "getup"}:
            artifact_names.extend(["joint_positions.png", "base_velocities.png"])
        artifacts = []
        for name in artifact_names:
            artifact = output_dir / name
            artifacts.append({"name": name, "exists": artifact.exists(), "bytes": artifact.stat().st_size if artifact.exists() else 0})
        payload.update(
            {
                "elapsed_wall_s": elapsed,
                "physics": stats,
                "inference": {
                    "calls": policy.calls,
                    "input_shapes": [list(shape) for shape in sorted(policy.input_shapes)],
                    "all_outputs_finite": policy.all_finite,
                    "max_abs_output": policy.max_abs_output,
                    "active_providers": policy.session.get_providers(),
                },
                "resolved_config": {
                    "mujoco_model_path": str(Path(cfg.sim_config.mujoco_model_path).resolve()),
                    "dt": cfg.sim_config.dt,
                    "decimation": cfg.sim_config.decimation,
                    "num_observations": cfg.robot_config.num_observations,
                    "num_actions": cfg.robot_config.num_actions,
                },
                "artifacts": artifacts,
            }
        )
        passed = (
            stats["all_state_finite"]
            and stats["mj_step_calls"] > 0
            and policy.calls > 0
            and policy.all_finite
            and all(item["exists"] and item["bytes"] > 0 for item in artifacts)
        )
        payload["result"] = "PASS" if passed else "FAIL"
    except Exception as exc:
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if module is not None:
            module.mujoco.mj_step = original_mj_step
        os.chdir(original_cwd)

    evidence_path = output_dir / "result.json"
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "case": args.case, "output": str(evidence_path)}, ensure_ascii=False))
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
