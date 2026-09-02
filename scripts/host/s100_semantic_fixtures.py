#!/usr/bin/env python3
"""Deterministic, contract-aware fixtures for the Roboto Origin S100 port.

The official deployment code does not consume independent Gaussian features:
gravity is a unit vector, commands are bounded, history frames are ordered and
correlated, motion policies consume the shipped motion files, and the depth
encoder consumes normalized depth history in [0, 1].  This module mirrors that
observable contract without opening a robot, sensor, transport, or ROS graph.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort


JOINT_COUNT = 23
JOINT_DEFAULT = np.asarray(
    [
        0.0, 0.0, -0.1, 0.3, -0.2, 0.0,
        0.0, 0.0, -0.1, 0.3, -0.2, 0.0, 0.0,
        0.18, 0.06, 0.0, 0.78, 0.0,
        0.18, -0.06, 0.0, 0.78, 0.0,
    ],
    dtype=np.float32,
)

BASE_FIELDS = (
    ("ang_vel", 3),
    ("gravity_b", 3),
    ("cmd_vel", 3),
    ("dof_pos", JOINT_COUNT),
    ("dof_vel", JOINT_COUNT),
    ("last_action", JOINT_COUNT),
)

MOTION_FILES = {
    "policy_dance0": "dance0.npz",
    "policy_dance1": "dance1.npz",
    "policy_getup": "getup.npz",
    "policy_wave": "wave.npz",
}

CONFIG_FILES = {
    "policy": "default.yaml",
    "policy_amp": "amp.yaml",
    "policy_attn_enc": "attn_enc.yaml",
    "policy_dance0": "beyondmimic.yaml",
    "policy_dance1": "beyondmimic.yaml",
    "policy_getup": "getup.yaml",
    "policy_interrupt": "interrupt.yaml",
    "policy_parkour": "parkour.yaml",
    "policy_wave": "beyondmimic.yaml",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _depth_histories(count: int, seed: int) -> np.ndarray:
    """Generate smooth normalized depth histories matching 1x8x18x32."""

    rng = np.random.default_rng(seed)
    yy, xx = np.meshgrid(
        np.linspace(-1.0, 1.0, 18, dtype=np.float32),
        np.linspace(-1.0, 1.0, 32, dtype=np.float32),
        indexing="ij",
    )
    samples = np.empty((count, 8, 18, 32), dtype=np.float32)
    for sample_index in range(count):
        base = rng.uniform(0.22, 0.88)
        slope_x = rng.uniform(-0.18, 0.18)
        slope_y = rng.uniform(-0.16, 0.16)
        bump_x = rng.uniform(-0.65, 0.65)
        bump_y = rng.uniform(-0.55, 0.55)
        bump_width = rng.uniform(0.12, 0.45)
        bump_height = rng.uniform(-0.22, 0.22)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        for history_index in range(8):
            drift = (history_index - 3.5) * rng.uniform(-0.006, 0.006)
            terrain = base + slope_x * xx + slope_y * yy + drift
            bump = bump_height * np.exp(
                -((xx - bump_x) ** 2 + (yy - bump_y) ** 2) / bump_width
            )
            ripple = 0.025 * np.sin(3.0 * xx + 2.0 * yy + phase + history_index * 0.08)
            frame = np.clip(terrain + bump + ripple, 0.0, 1.0).astype(np.float32)
            # Deterministic sparse invalid-depth holes, as preserved by the official preprocessor.
            if (sample_index + history_index) % 7 == 0:
                row = (sample_index * 3 + history_index) % 18
                col = (sample_index * 5 + history_index * 2) % 32
                frame[max(0, row - 1) : min(18, row + 2), col : min(32, col + 2)] = 0.0
            samples[sample_index, history_index] = frame

    # Explicit startup/boundary cases remain within the official normalized contract.
    if count > 0:
        samples[0].fill(0.0)
    if count > 1:
        samples[1].fill(0.5)
    if count > 2:
        samples[2] = np.broadcast_to(((xx + 1.0) * 0.5)[None], (8, 18, 32))
    if count > 3:
        samples[3].fill(1.0)
    return samples


def _gravity_from_roll_pitch(roll: np.ndarray, pitch: np.ndarray) -> np.ndarray:
    gravity = np.stack(
        [
            np.sin(pitch),
            -np.sin(roll) * np.cos(pitch),
            -np.cos(roll) * np.cos(pitch),
        ],
        axis=1,
    )
    return gravity.astype(np.float32)


def _state_fields(
    frame_count: int,
    seed: int,
    *,
    cmd_bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    ang_scale: float = 1.0,
    dof_vel_scale: float = 1.0,
) -> dict[str, np.ndarray]:
    """Create a smooth, bounded 50 Hz state trajectory in official field order."""

    rng = np.random.default_rng(seed)
    time_axis = np.arange(frame_count, dtype=np.float32) * 0.02
    joint_phase = rng.uniform(-np.pi, np.pi, JOINT_COUNT).astype(np.float32)
    joint_frequency = rng.uniform(0.35, 1.35, JOINT_COUNT).astype(np.float32)
    joint_amplitude = rng.uniform(0.035, 0.34, JOINT_COUNT).astype(np.float32)
    # Keep torso/neck excursions conservative while exercising legs and arms.
    joint_amplitude[[0, 1, 6, 7, 12]] *= 0.55
    angle = time_axis[:, None] * joint_frequency[None] * (2.0 * np.pi) + joint_phase[None]
    dof_pos = joint_amplitude[None] * np.sin(angle)
    dof_pos += 0.025 * np.sin(0.37 * angle + joint_phase[None] * 0.5)
    dof_vel = (
        joint_amplitude[None]
        * joint_frequency[None]
        * (2.0 * np.pi)
        * np.cos(angle)
    )
    dof_vel += 0.025 * 0.37 * joint_frequency[None] * (2.0 * np.pi) * np.cos(
        0.37 * angle + joint_phase[None] * 0.5
    )

    roll = 0.10 * np.sin(time_axis * 1.1 + rng.uniform(-np.pi, np.pi))
    pitch = 0.12 * np.sin(time_axis * 0.85 + rng.uniform(-np.pi, np.pi))
    yaw_rate = 0.45 * np.sin(time_axis * 0.55 + rng.uniform(-np.pi, np.pi))
    roll_rate = np.gradient(roll, 0.02)
    pitch_rate = np.gradient(pitch, 0.02)
    ang_vel = np.stack([roll_rate, pitch_rate, yaw_rate], axis=1).astype(np.float32)
    ang_vel *= np.float32(ang_scale)

    command = np.empty((frame_count, 3), dtype=np.float32)
    for axis, (lower, upper) in enumerate(cmd_bounds):
        center = (lower + upper) * 0.5
        radius = (upper - lower) * 0.45
        command[:, axis] = center + radius * np.sin(
            time_axis * (0.31 + axis * 0.13) + rng.uniform(-np.pi, np.pi)
        )
    last_action = np.clip(dof_pos / 0.25, -4.0, 4.0).astype(np.float32)

    fields = {
        "ang_vel": ang_vel,
        "gravity_b": _gravity_from_roll_pitch(roll, pitch),
        "cmd_vel": command,
        "dof_pos": dof_pos.astype(np.float32),
        "dof_vel": (dof_vel * np.float32(dof_vel_scale)).astype(np.float32),
        "last_action": last_action,
    }
    # Exact initial standing state reflects reset_policy_runtime plus the first sensor read.
    for value in fields.values():
        value[0].fill(0.0)
    fields["gravity_b"][0, 2] = -1.0
    return fields


def _window_fields(fields: dict[str, np.ndarray], count: int, history: int) -> list[dict[str, np.ndarray]]:
    windows: list[dict[str, np.ndarray]] = []
    for sample_index in range(count):
        windows.append(
            {
                name: values[sample_index : sample_index + history]
                for name, values in fields.items()
            }
        )
    return windows


def _pack_dense(
    windows: list[dict[str, np.ndarray]],
    *,
    history: int,
    order: str,
    extra_per_frame: dict[str, np.ndarray] | None = None,
) -> np.ndarray:
    packed: list[np.ndarray] = []
    field_names = [name for name, _ in BASE_FIELDS]
    for sample_index, window in enumerate(windows):
        if extra_per_frame is not None:
            local = dict(window)
            for name, values in extra_per_frame.items():
                local[name] = values[sample_index : sample_index + history]
            names = [*field_names, *extra_per_frame.keys()]
        else:
            local = window
            names = field_names
        if order == "frame_major":
            vector = np.concatenate(
                [np.concatenate([local[name][frame] for name in names]) for frame in range(history)]
            )
        elif order == "obs_major":
            vector = np.concatenate([local[name].reshape(-1) for name in names])
        else:
            raise ValueError(f"unsupported stack order: {order}")
        packed.append(vector.astype(np.float32, copy=False))
    return np.stack(packed)


def _elevation_maps(count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.meshgrid(
        np.linspace(-1.0, 1.0, 11, dtype=np.float32),
        np.linspace(-1.0, 1.0, 17, dtype=np.float32),
        indexing="ij",
    )
    maps = np.empty((count, 187), dtype=np.float32)
    for index in range(count):
        slope = rng.uniform(-0.12, 0.12) * xx + rng.uniform(-0.12, 0.12) * yy
        obstacle = rng.uniform(-0.22, 0.22) * np.exp(
            -((xx - rng.uniform(-0.6, 0.6)) ** 2 + (yy - rng.uniform(-0.6, 0.6)) ** 2)
            / rng.uniform(0.10, 0.38)
        )
        maps[index] = np.clip(slope + obstacle, -0.35, 0.35).reshape(-1)
    if count:
        maps[0].fill(0.0)
    return maps


def _quat_wxyz_inverse_gravity(quat: np.ndarray) -> np.ndarray:
    """Rotate world [0,0,-1] into body coordinates for Isaac-style wxyz."""

    q = quat.astype(np.float64, copy=False)
    norm = np.linalg.norm(q, axis=1, keepdims=True)
    q = q / np.maximum(norm, 1e-12)
    w, x, y, z = (q[:, index] for index in range(4))
    # Third row of body->world rotation dotted with world gravity, equivalent to R^T g.
    gravity = np.stack(
        [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), -(1.0 - 2.0 * (x * x + y * y))],
        axis=1,
    )
    return gravity.astype(np.float32)


def _motion_samples(model_name: str, count: int, seed: int, upstream: Path) -> tuple[np.ndarray, dict[str, object]]:
    motion_name = MOTION_FILES[model_name]
    motion_path = upstream / "src" / "inference" / "robots" / "rpo" / "motions" / motion_name
    motion = np.load(motion_path, allow_pickle=False)
    frame_count = int(motion["joint_pos"].shape[0])
    rng = np.random.default_rng(seed)
    # Seeded coverage over the full official sequence; replacement is never needed for shipped files.
    indices = np.sort(rng.choice(frame_count, size=count, replace=count > frame_count))
    joint_pos = motion["joint_pos"][indices].astype(np.float32)
    joint_vel = motion["joint_vel"][indices].astype(np.float32)
    motion_command = np.concatenate([joint_pos, joint_vel], axis=1)
    dof_pos = joint_pos - JOINT_DEFAULT[None]
    dof_vel = joint_vel
    last_action = np.clip(dof_pos / 0.25, -10.0, 10.0).astype(np.float32)
    ang_vel = np.clip(motion["body_ang_vel_w"][indices, 0, :], -6.0, 6.0).astype(np.float32)
    gravity = _quat_wxyz_inverse_gravity(motion["body_quat_w"][indices, 0, :])
    samples = np.concatenate(
        [motion_command, ang_vel, gravity, dof_pos, dof_vel, last_action], axis=1
    ).astype(np.float32)
    return samples, {
        "profile": "official_motion_npz_plus_shadow_state",
        "motion_file": str(motion_path.relative_to(upstream)).replace("\\", "/"),
        "motion_sha256": sha256(motion_path),
        "motion_frame_count": frame_count,
        "selected_frame_count": count,
        "layout": "motion_command:46,ang_vel:3,gravity_b:3,dof_pos:23,dof_vel:23,last_action:23",
    }


def semantic_samples(
    model_name: str,
    count: int,
    seed: int,
    upstream: Path,
    encoder_session: ort.InferenceSession,
) -> tuple[np.ndarray, dict[str, object]]:
    """Return deterministic samples and provenance for one official model."""

    if model_name == "encoder":
        samples = _depth_histories(count, seed)
        provenance: dict[str, object] = {
            "profile": "official_depth_normalized_history",
            "layout": "8 history taps x 18 x 32 crop",
            "bounds": [0.0, 1.0],
            "source": "src/camera/src/depth_provider.cpp",
        }
    elif model_name in MOTION_FILES:
        samples, provenance = _motion_samples(model_name, count, seed, upstream)
    else:
        history = {
            "policy": 10,
            "policy_amp": 3,
            "policy_attn_enc": 5,
            "policy_interrupt": 10,
            "policy_parkour": 8,
        }[model_name]
        cmd_bounds = (
            ((-0.5, 2.0), (-0.6, 0.6), (-1.57, 1.57))
            if model_name == "policy_amp"
            else ((-0.4, 0.6), (-0.4, 0.4), (-0.8, 0.8))
        )
        if model_name == "policy_parkour":
            cmd_bounds = ((-0.5, 0.5), (-0.5, 0.5), (-1.0, 1.0))
        fields = _state_fields(
            count + history - 1,
            seed,
            cmd_bounds=cmd_bounds,
            ang_scale=0.25 if model_name == "policy_parkour" else 1.0,
            dof_vel_scale=0.05 if model_name == "policy_parkour" else 1.0,
        )
        windows = _window_fields(fields, count, history)
        if model_name == "policy":
            samples = _pack_dense(windows, history=history, order="frame_major")
            profile = "official_state_history_frame_major"
        elif model_name == "policy_amp":
            samples = _pack_dense(windows, history=history, order="obs_major")
            profile = "official_state_history_obs_major"
        elif model_name == "policy_interrupt":
            interrupt = np.zeros((count + history - 1, 1), dtype=np.float32)
            interrupt[(np.arange(len(interrupt)) // 11) % 2 == 1] = 1.0
            samples = _pack_dense(
                windows,
                history=history,
                order="frame_major",
                extra_per_frame={"interrupt": interrupt},
            )
            profile = "official_state_history_plus_boolean_interrupt"
        elif model_name == "policy_attn_enc":
            elevation = _elevation_maps(count, seed + 1)
            packed = []
            for sample_index, window in enumerate(windows):
                frame_vectors = [
                    np.concatenate([window[name][frame] for name, _ in BASE_FIELDS])
                    for frame in range(history)
                ]
                packed.append(np.concatenate([*frame_vectors, elevation[sample_index]]))
            samples = np.stack(packed).astype(np.float32)
            profile = "official_sparse_frame_major_plus_current_elevation"
        elif model_name == "policy_parkour":
            depth = _depth_histories(count, seed + 2)
            encoder_input = encoder_session.get_inputs()[0].name
            encoder_output = encoder_session.get_outputs()[0].name
            perception = np.concatenate(
                [
                    encoder_session.run([encoder_output], {encoder_input: sample[None]})[0]
                    for sample in depth
                ],
                axis=0,
            ).astype(np.float32)
            packed = []
            for sample_index, window in enumerate(windows):
                obs_major = np.concatenate([window[name].reshape(-1) for name, _ in BASE_FIELDS])
                packed.append(np.concatenate([obs_major, perception[sample_index]]))
            samples = np.stack(packed).astype(np.float32)
            profile = "official_sparse_obs_major_plus_depth_encoder_latent"
        else:
            raise AssertionError(model_name)
        config_name = CONFIG_FILES[model_name]
        config_path = upstream / "src" / "inference" / "robots" / "rpo" / "configs" / config_name
        provenance = {
            "profile": profile,
            "config_file": str(config_path.relative_to(upstream)).replace("\\", "/"),
            "config_sha256": sha256(config_path),
            "history_frames": history,
            "layout_fields": [{"name": name, "size": size} for name, size in BASE_FIELDS],
        }

    if samples.shape[0] != count or not np.isfinite(samples).all():
        raise RuntimeError(f"invalid semantic fixture for {model_name}: {samples.shape}")
    provenance.update(
        {
            "seed": seed,
            "count": count,
            "observed_min": float(np.min(samples)),
            "observed_max": float(np.max(samples)),
            "observed_mean": float(np.mean(samples)),
            "observed_std": float(np.std(samples)),
            "external_device_access": False,
            "control_output": False,
        }
    )
    return samples.astype(np.float32, copy=False), provenance
