"""Deterministic synthetic BEV sequences for offline algorithm validation.

The generator deliberately has no ROS or device dependency.  It creates small
moving discs on a grid, their future occupancy, current-cell velocity, and a
change-region target used by the uncertainty head.  It is an algorithm fixture,
not a claim about the geometry or statistics of the future robot sensors.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BevBatch:
    history: np.ndarray
    future: np.ndarray
    flow: np.ndarray
    dynamic_mask: np.ndarray
    uncertainty: np.ndarray


def _disc(grid: np.ndarray, x: int, y: int, radius: int, value: float = 1.0) -> None:
    height, width = grid.shape
    yy, xx = np.ogrid[:height, :width]
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= radius**2
    grid[mask] = np.maximum(grid[mask], value)


def _dilate(binary: np.ndarray) -> np.ndarray:
    padded = np.pad(binary, ((1, 1), (1, 1)), mode="constant")
    views = [
        padded[dy : dy + binary.shape[0], dx : dx + binary.shape[1]]
        for dy in range(3)
        for dx in range(3)
    ]
    return np.maximum.reduce(views)


def generate_dataset(
    samples: int,
    *,
    seed: int,
    grid_size: int = 32,
    history_frames: int = 4,
    future_frames: int = 3,
    sensor_noise: bool = True,
) -> BevBatch:
    """Generate a fixed dataset with analytically known future motion."""

    rng = np.random.default_rng(seed)
    history = np.zeros((samples, history_frames, grid_size, grid_size), dtype=np.float32)
    future = np.zeros((samples, future_frames, grid_size, grid_size), dtype=np.float32)
    flow = np.zeros((samples, 2, grid_size, grid_size), dtype=np.float32)
    dynamic_mask = np.zeros((samples, 1, grid_size, grid_size), dtype=np.float32)
    uncertainty = np.zeros((samples, 1, grid_size, grid_size), dtype=np.float32)
    velocity_choices = np.asarray(
        [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)],
        dtype=np.int32,
    )

    for sample in range(samples):
        objects: list[tuple[int, int, int, int, int]] = []
        for _ in range(int(rng.integers(1, 4))):
            vx, vy = velocity_choices[int(rng.integers(len(velocity_choices)))]
            radius = int(rng.integers(1, 3))
            margin_x = radius + abs(int(vx)) * max(history_frames, future_frames) + 2
            margin_y = radius + abs(int(vy)) * max(history_frames, future_frames) + 2
            x = int(rng.integers(margin_x, grid_size - margin_x))
            y = int(rng.integers(margin_y, grid_size - margin_y))
            objects.append((x, y, int(vx), int(vy), radius))

        for frame in range(history_frames):
            time_index = frame - history_frames + 1
            for x, y, vx, vy, radius in objects:
                _disc(history[sample, frame], x + time_index * vx, y + time_index * vy, radius)

        for horizon in range(future_frames):
            time_index = horizon + 1
            for x, y, vx, vy, radius in objects:
                _disc(future[sample, horizon], x + time_index * vx, y + time_index * vy, radius)

        flow_count = np.zeros((grid_size, grid_size), dtype=np.float32)
        for x, y, vx, vy, radius in objects:
            object_mask = np.zeros((grid_size, grid_size), dtype=np.float32)
            _disc(object_mask, x, y, radius)
            flow[sample, 0] += object_mask * vx
            flow[sample, 1] += object_mask * vy
            flow_count += object_mask
        occupied = flow_count > 0
        flow[sample, 0, occupied] /= flow_count[occupied]
        flow[sample, 1, occupied] /= flow_count[occupied]
        dynamic_mask[sample, 0, occupied] = 1.0

        changed = np.max(np.abs(future[sample] - history[sample, -1]), axis=0)
        uncertainty[sample, 0] = _dilate((changed > 0).astype(np.float32))

        if sensor_noise:
            # Sparse flips and a small history-only occlusion make the model
            # learn temporal evidence instead of simply copying the last frame.
            flips = rng.random(history[sample].shape) < 0.0015
            history[sample][flips] = 1.0 - history[sample][flips]
            if rng.random() < 0.20:
                width = int(rng.integers(2, 5))
                x0 = int(rng.integers(0, grid_size - width))
                y0 = int(rng.integers(0, grid_size - width))
                history[sample, -1, y0 : y0 + width, x0 : x0 + width] = 0.0

    return BevBatch(history, future, flow, dynamic_mask, uncertainty)
