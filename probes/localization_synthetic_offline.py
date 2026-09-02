#!/usr/bin/env python3

"""Drive RoboParty FAST-LIO2 with deterministic synthetic Ouster/IMU data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

import numpy as np
import rclpy
from nav_msgs.msg import Odometry, Path as NavPath
from rclpy.node import Node
from sensor_msgs.msg import Imu, PointCloud2, PointField


POINT_DTYPE = np.dtype(
    {
        "names": ["x", "y", "z", "intensity", "t", "reflectivity", "ring", "ambient", "range"],
        "formats": ["<f4", "<f4", "<f4", "<f4", "<u4", "<u2", "u1", "<u2", "<u4"],
        "offsets": [0, 4, 8, 12, 16, 20, 22, 24, 28],
        "itemsize": 32,
    }
)


def room_points() -> np.ndarray:
    horizontal = np.linspace(-5.0, 5.0, 35, dtype=np.float32)
    vertical = np.linspace(-1.0, 2.5, 18, dtype=np.float32)
    points: list[tuple[float, float, float]] = []
    for x in horizontal:
        for y in horizontal:
            if x * x + y * y > 0.4 * 0.4:
                points.append((float(x), float(y), -1.0))
    for coordinate in horizontal:
        for z in vertical:
            points.extend(
                [
                    (-5.0, float(coordinate), float(z)),
                    (5.0, float(coordinate), float(z)),
                    (float(coordinate), -5.0, float(z)),
                    (float(coordinate), 5.0, float(z)),
                ]
            )
    # An asymmetric obstacle prevents a rotationally ambiguous synthetic room.
    for y in np.linspace(-1.2, 1.2, 20, dtype=np.float32):
        for z in np.linspace(-0.8, 1.2, 16, dtype=np.float32):
            points.append((2.0, float(y), float(z)))
    return np.asarray(points, dtype=np.float32)


def ouster_payload(points: np.ndarray) -> np.ndarray:
    output = np.zeros(points.shape[0], dtype=POINT_DTYPE)
    output["x"] = points[:, 0]
    output["y"] = points[:, 1]
    output["z"] = points[:, 2]
    output["intensity"] = 100.0 + np.arange(points.shape[0], dtype=np.float32) % 50.0
    output["t"] = np.linspace(0, 90_000_000, points.shape[0], dtype=np.uint32)
    output["reflectivity"] = 100
    output["ring"] = np.arange(points.shape[0], dtype=np.uint32) % 64
    output["ambient"] = 5
    output["range"] = (np.sqrt(np.sum(points * points, axis=1)) * 1000.0).astype(np.uint32)
    return output


class LocalizationProbe(Node):
    def __init__(self) -> None:
        super().__init__("roboto_synthetic_localization_probe")
        self.imu_publisher = self.create_publisher(Imu, "/roboto_offline/synthetic_imu", 200)
        self.cloud_publisher = self.create_publisher(
            PointCloud2, "/roboto_offline/synthetic_points", 10
        )
        self.create_subscription(Odometry, "/roboto_offline/odometry", self._on_odom, 50)
        self.create_subscription(
            PointCloud2, "/roboto_offline/cloud_registered", self._on_registered, 10
        )
        self.create_subscription(NavPath, "/roboto_offline/path", self._on_path, 10)
        self.points = room_points()
        self.payload = ouster_payload(self.points)
        self.odom_records: list[dict] = []
        self.registered_records: list[dict] = []
        self.path_lengths: list[int] = []

    def publish_imu(self) -> None:
        message = Imu()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "synthetic_imu"
        message.orientation_covariance[0] = -1.0
        message.angular_velocity.x = 0.0
        message.angular_velocity.y = 0.0
        message.angular_velocity.z = 0.0
        message.linear_acceleration.x = 0.0
        message.linear_acceleration.y = 0.0
        message.linear_acceleration.z = 9.80665
        self.imu_publisher.publish(message)

    def publish_cloud(self) -> None:
        message = PointCloud2()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "synthetic_lidar"
        message.height = 1
        message.width = int(self.payload.shape[0])
        message.fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
            PointField(name="intensity", offset=12, datatype=PointField.FLOAT32, count=1),
            PointField(name="t", offset=16, datatype=PointField.UINT32, count=1),
            PointField(name="reflectivity", offset=20, datatype=PointField.UINT16, count=1),
            PointField(name="ring", offset=22, datatype=PointField.UINT8, count=1),
            PointField(name="ambient", offset=24, datatype=PointField.UINT16, count=1),
            PointField(name="range", offset=28, datatype=PointField.UINT32, count=1),
        ]
        message.is_bigendian = False
        message.point_step = 32
        message.row_step = message.point_step * message.width
        message.data = self.payload.tobytes(order="C")
        message.is_dense = True
        self.cloud_publisher.publish(message)

    def _on_odom(self, message: Odometry) -> None:
        pose = message.pose.pose
        twist = message.twist.twist
        values = [
            pose.position.x,
            pose.position.y,
            pose.position.z,
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
            twist.linear.x,
            twist.linear.y,
            twist.linear.z,
        ]
        self.odom_records.append(
            {
                "values": values,
                "finite": all(math.isfinite(value) for value in values),
                "received_monotonic": time.monotonic(),
            }
        )

    def _on_registered(self, message: PointCloud2) -> None:
        self.registered_records.append(
            {"width": int(message.width), "height": int(message.height), "bytes": len(message.data)}
        )

    def _on_path(self, message: NavPath) -> None:
        self.path_lengths.append(len(message.poses))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=8.0)
    args = parser.parse_args()

    payload = {
        "schema_version": 1,
        "scope": "official FAST-LIO2 mapping with synthetic Ouster/IMU; no sensor or robot",
        "status": "FAIL",
    }
    started = time.monotonic()
    rclpy.init()
    node = LocalizationProbe()
    imu_count = 0
    cloud_count = 0
    try:
        next_imu = time.monotonic()
        next_cloud = next_imu
        deadline = next_imu + args.duration
        while time.monotonic() < deadline:
            now = time.monotonic()
            while now >= next_imu:
                node.publish_imu()
                imu_count += 1
                next_imu += 0.005
            if now >= next_cloud:
                node.publish_cloud()
                cloud_count += 1
                next_cloud += 0.1
            rclpy.spin_once(node, timeout_sec=0.001)
        drain_deadline = time.monotonic() + 2.0
        while time.monotonic() < drain_deadline:
            node.publish_imu()
            imu_count += 1
            rclpy.spin_once(node, timeout_sec=0.005)

        translations = [record["values"][:3] for record in node.odom_records]
        translation_norms = [math.sqrt(sum(value * value for value in xyz)) for xyz in translations]
        intervals = [
            right["received_monotonic"] - left["received_monotonic"]
            for left, right in zip(node.odom_records, node.odom_records[1:])
        ]
        checks = {
            "imu_published": imu_count >= int(args.duration * 150),
            "clouds_published": cloud_count >= int(args.duration * 8),
            "odometry_output": len(node.odom_records) >= 3,
            "odometry_finite": bool(node.odom_records)
            and all(record["finite"] for record in node.odom_records),
            "registered_cloud_output": bool(node.registered_records)
            and all(item["width"] > 0 and item["bytes"] > 0 for item in node.registered_records),
            "stationary_translation_bounded": bool(translation_norms)
            and max(translation_norms) < 1.0,
        }
        payload.update(
            {
                "input": {
                    "imu_messages": imu_count,
                    "cloud_messages": cloud_count,
                    "points_per_cloud": int(node.points.shape[0]),
                    "cloud_sha256": hashlib.sha256(node.payload.tobytes()).hexdigest(),
                },
                "output": {
                    "odometry_messages": len(node.odom_records),
                    "registered_cloud_messages": len(node.registered_records),
                    "path_messages": len(node.path_lengths),
                    "max_path_poses": max(node.path_lengths) if node.path_lengths else 0,
                    "max_translation_norm_m": max(translation_norms) if translation_norms else None,
                    "last_odometry": node.odom_records[-1]["values"] if node.odom_records else None,
                    "median_odometry_interval_ms": statistics.median(intervals) * 1000.0
                    if intervals
                    else None,
                },
                "checks": checks,
                "elapsed_seconds": time.monotonic() - started,
                "status": "PASS" if all(checks.values()) else "FAIL",
            }
        )
    except Exception as error:
        payload["error"] = f"{type(error).__name__}: {error}"
        payload["elapsed_seconds"] = time.monotonic() - started
    finally:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        node.destroy_node()
        rclpy.shutdown()

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
