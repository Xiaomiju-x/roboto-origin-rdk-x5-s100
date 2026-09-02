#!/usr/bin/env python3
"""Publish a deterministic static cloud and verify passive KISS-ICP odometry."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2, PointField


def make_asymmetric_room() -> np.ndarray:
    """Use the exact XYZ geometry from the accepted FAST-LIO2 fixture."""

    horizontal = np.linspace(-5.0, 5.0, 35, dtype=np.float32)
    vertical = np.linspace(-1.0, 2.5, 18, dtype=np.float32)
    values: list[tuple[float, float, float]] = []
    for x in horizontal:
        for y in horizontal:
            if x * x + y * y > 0.4 * 0.4:
                values.append((float(x), float(y), -1.0))
    for coordinate in horizontal:
        for z in vertical:
            values.extend(
                [
                    (-5.0, float(coordinate), float(z)),
                    (5.0, float(coordinate), float(z)),
                    (float(coordinate), -5.0, float(z)),
                    (float(coordinate), 5.0, float(z)),
                ]
            )
    for y in np.linspace(-1.2, 1.2, 20, dtype=np.float32):
        for z in np.linspace(-0.8, 1.2, 16, dtype=np.float32):
            values.append((2.0, float(y), float(z)))
    return np.asarray(values, dtype=np.float32)


def point_cloud_message(node: Node, points: np.ndarray, frame_id: str) -> PointCloud2:
    message = PointCloud2()
    message.header.stamp = node.get_clock().now().to_msg()
    message.header.frame_id = frame_id
    message.height = 1
    message.width = int(points.shape[0])
    message.fields = [
        PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
        PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
        PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
    ]
    message.is_bigendian = False
    message.point_step = 12
    message.row_step = message.point_step * message.width
    message.is_dense = True
    message.data = np.ascontiguousarray(points.astype("<f4", copy=False)).tobytes()
    return message


class SyntheticProbe(Node):
    def __init__(self, frames: int, rate_hz: float, motion_step_m: float) -> None:
        super().__init__("kiss_icp_synthetic_probe")
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=20,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.publisher = self.create_publisher(PointCloud2, "/roboto_upgrade/synthetic_points", qos)
        self.subscription = self.create_subscription(
            Odometry, "/roboto_upgrade/kiss/odometry", self._odometry_callback, 20
        )
        self.points = make_asymmetric_room()
        self.motion_step_m = motion_step_m
        self.frames = frames
        self.sent = 0
        self.positions: list[list[float]] = []
        self.quaternions: list[list[float]] = []
        self.timer = self.create_timer(1.0 / rate_hz, self._publish)

    def _publish(self) -> None:
        if self.sent >= self.frames:
            return
        scan = self.points.copy()
        # A sensor translated +X observes static world points shifted -X in
        # its local frame.  KISS-ICP should therefore estimate a +X pose.
        scan[:, 0] -= self.sent * self.motion_step_m
        self.publisher.publish(point_cloud_message(self, scan, "synthetic_lidar"))
        self.sent += 1

    def _odometry_callback(self, message: Odometry) -> None:
        position = message.pose.pose.position
        orientation = message.pose.pose.orientation
        self.positions.append([position.x, position.y, position.z])
        self.quaternions.append([orientation.x, orientation.y, orientation.z, orientation.w])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=36)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--timeout-s", type=float, default=10.0)
    parser.add_argument("--motion-step-m", type=float, default=0.0)
    args = parser.parse_args()

    rclpy.init()
    node = SyntheticProbe(args.frames, args.rate_hz, args.motion_step_m)
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.timeout_s:
            rclpy.spin_once(node, timeout_sec=0.05)
            if node.sent >= args.frames and len(node.positions) >= args.frames - 3:
                break
    finally:
        node.destroy_node()
        rclpy.shutdown()

    positions = np.asarray(node.positions, dtype=np.float64)
    quaternions = np.asarray(node.quaternions, dtype=np.float64)
    finite = bool(positions.size and np.isfinite(positions).all() and np.isfinite(quaternions).all())
    relative_positions = positions - positions[0] if positions.size else positions
    if positions.size:
        displacement = np.linalg.norm(relative_positions, axis=1)
        max_displacement = float(displacement.max())
        final_displacement = float(displacement[-1])
    else:
        max_displacement = float("inf")
        final_displacement = float("inf")
    criteria: dict[str, bool] = {
        "all_frames_sent": node.sent == args.frames,
        "odometry_frames_gte_30": len(node.positions) >= 30,
        "outputs_finite": finite,
    }
    trajectory = None
    if args.motion_step_m == 0.0:
        criteria["stationary_max_displacement_lte_2cm"] = max_displacement <= 0.02
    elif positions.size:
        expected_x = np.arange(len(relative_positions), dtype=np.float64) * args.motion_step_m
        x_error = relative_positions[:, 0] - expected_x
        trajectory = {
            "motion_step_m": args.motion_step_m,
            "expected_final_x_m": float(expected_x[-1]),
            "estimated_final_x_m": float(relative_positions[-1, 0]),
            "final_x_error_m": float(abs(x_error[-1])),
            "x_rmse_m": float(np.sqrt(np.mean(x_error**2))),
            "max_lateral_error_m": float(np.max(np.linalg.norm(relative_positions[:, 1:3], axis=1))),
        }
        criteria.update(
            {
                "motion_final_error_lte_10cm": trajectory["final_x_error_m"] <= 0.10,
                "motion_x_rmse_lte_8cm": trajectory["x_rmse_m"] <= 0.08,
                "motion_lateral_error_lte_5cm": trajectory["max_lateral_error_m"] <= 0.05,
            }
        )
    else:
        criteria["motion_output_present"] = False
    passed = all(criteria.values())
    payload = {
        "schema_version": 1,
        "scope": (
            "deterministic synthetic static PointCloud2; no lidar, IMU, TF authority, or control"
            if args.motion_step_m == 0.0
            else "deterministic synthetic known-translation PointCloud2; no lidar, IMU, TF authority, or control"
        ),
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fixture_points": int(node.points.shape[0]),
        "fixture_xyz_sha256": hashlib.sha256(node.points.tobytes(order="C")).hexdigest(),
        "frames_sent": node.sent,
        "odometry_frames": len(node.positions),
        "max_relative_displacement_m": max_displacement,
        "final_relative_displacement_m": final_displacement,
        "trajectory": trajectory,
        "criteria": criteria,
        "result": "PASS" if passed else "FAIL",
    }
    if args.motion_step_m == 0.0:
        payload["max_stationary_displacement_m"] = max_displacement
        payload["final_stationary_displacement_m"] = final_displacement
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
