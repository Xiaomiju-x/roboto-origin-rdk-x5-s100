#!/usr/bin/env python3

"""Feed synthetic Z16 frames through RoboParty's compiled X5 depth node."""

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
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Float32MultiArray
from std_srvs.srv import Trigger


WIDTH = 480
HEIGHT = 270


def synthetic_depth_mm(sequence: int, dynamic: bool) -> np.ndarray:
    rows = np.arange(HEIGHT, dtype=np.uint32)[:, None]
    cols = np.arange(WIDTH, dtype=np.uint32)[None, :]
    phase = sequence * 17 if dynamic else 0
    values = 250 + ((rows * 11 + cols * 7 + phase) % 3100)
    holes = ((rows * 13 + cols * 5) % 97) == 0
    values[holes] = 0
    return values.astype(np.uint16)


def fingerprint(values: list[float]) -> str:
    array = np.asarray(values, dtype="<f4")
    return hashlib.sha256(array.tobytes()).hexdigest()


class DepthPipelineProbe(Node):
    def __init__(self) -> None:
        super().__init__("roboto_offline_depth_probe")
        best_effort = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        reliable = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.publisher = self.create_publisher(
            Image, "/camera/camera/depth/image_rect_raw", best_effort
        )
        self.create_subscription(Float32MultiArray, "/depth_obs", self._on_obs, best_effort)
        self.create_subscription(Image, "/debug_depth_vis/downsample", self._on_downsample, reliable)
        self.create_subscription(Image, "/debug_depth_vis/crop", self._on_crop, reliable)
        self.reset_client = self.create_client(Trigger, "/clear_depth_history")
        self.phase = "waiting"
        self.obs_records: list[dict] = []
        self.fixed_outputs: list[list[float]] = []
        self.dynamic_outputs: list[list[float]] = []
        self.downsample_records: list[dict] = []
        self.crop_records: list[dict] = []

    def _on_obs(self, message: Float32MultiArray) -> None:
        values = [float(value) for value in message.data]
        record = {
            "received_monotonic": time.monotonic(),
            "phase": self.phase,
            "length": len(values),
            "finite": all(math.isfinite(value) for value in values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "sha256_float32_le": fingerprint(values) if values else None,
        }
        self.obs_records.append(record)
        if self.phase == "fixed":
            self.fixed_outputs.append(values)
        elif self.phase == "dynamic":
            self.dynamic_outputs.append(values)

    @staticmethod
    def _image_record(message: Image) -> dict:
        if message.encoding != "32FC1":
            return {
                "encoding": message.encoding,
                "width": int(message.width),
                "height": int(message.height),
                "finite": False,
                "error": "expected 32FC1",
            }
        values = np.frombuffer(bytes(message.data), dtype="<f4")
        return {
            "encoding": message.encoding,
            "width": int(message.width),
            "height": int(message.height),
            "count": int(values.size),
            "finite": bool(np.isfinite(values).all()),
            "min": float(values.min()) if values.size else None,
            "max": float(values.max()) if values.size else None,
            "sha256_float32_le": hashlib.sha256(values.tobytes()).hexdigest(),
        }

    def _on_downsample(self, message: Image) -> None:
        if len(self.downsample_records) < 4:
            self.downsample_records.append(self._image_record(message))

    def _on_crop(self, message: Image) -> None:
        if len(self.crop_records) < 4:
            self.crop_records.append(self._image_record(message))

    def publish_frame(self, sequence: int, dynamic: bool) -> None:
        depth = synthetic_depth_mm(sequence, dynamic)
        message = Image()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "synthetic_depth_frame"
        message.height = HEIGHT
        message.width = WIDTH
        message.encoding = "16UC1"
        message.is_bigendian = False
        message.step = WIDTH * 2
        message.data = depth.tobytes(order="C")
        self.publisher.publish(message)


def spin_for(node: Node, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=min(0.02, max(0.0, deadline - time.monotonic())))


def publish_phase(
    node: DepthPipelineProbe,
    phase: str,
    frame_count: int,
    rate_hz: float,
    dynamic: bool,
) -> None:
    node.phase = phase
    period = 1.0 / rate_hz
    next_publish = time.monotonic()
    for sequence in range(frame_count):
        while time.monotonic() < next_publish:
            rclpy.spin_once(node, timeout_sec=min(0.005, next_publish - time.monotonic()))
        node.publish_frame(sequence, dynamic)
        next_publish += period
        rclpy.spin_once(node, timeout_sec=0.001)
    spin_for(node, 0.25)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--service-timeout", type=float, default=15.0)
    args = parser.parse_args()

    started = time.monotonic()
    payload = {
        "schema_version": 1,
        "scope": "official X5 depth_node with synthetic Z16 frames; no camera or robot",
        "status": "FAIL",
        "input_contract": {
            "encoding": "16UC1",
            "width": WIDTH,
            "height": HEIGHT,
            "fixed_generator": "250 + ((row*11 + col*7) % 3100), holes where (row*13 + col*5) % 97 == 0",
        },
    }
    rclpy.init()
    node = DepthPipelineProbe()
    try:
        if not node.reset_client.wait_for_service(timeout_sec=args.service_timeout):
            raise TimeoutError("clear_depth_history service unavailable")
        reset_future = node.reset_client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(node, reset_future, timeout_sec=args.service_timeout)
        reset_response = reset_future.result()
        if reset_response is None or not reset_response.success:
            raise RuntimeError("clear_depth_history service failed")

        publish_phase(node, "fixed", frame_count=25, rate_hz=25.0, dynamic=False)
        publish_phase(node, "dynamic", frame_count=50, rate_hz=25.0, dynamic=True)
        node.phase = "drain"
        spin_for(node, 0.5)

        fixed_hashes = [fingerprint(values) for values in node.fixed_outputs]
        dynamic_hashes = [fingerprint(values) for values in node.dynamic_outputs]
        receive_times = [record["received_monotonic"] for record in node.obs_records]
        intervals = [right - left for left, right in zip(receive_times, receive_times[1:])]
        all_outputs = node.fixed_outputs + node.dynamic_outputs
        checks = {
            "reset_service": True,
            "output_count": len(node.obs_records) >= 30,
            "output_shape_128": bool(all_outputs) and all(len(values) == 128 for values in all_outputs),
            "output_finite": bool(all_outputs)
            and all(math.isfinite(value) for values in all_outputs for value in values),
            "fixed_deterministic": len(fixed_hashes) >= 5 and len(set(fixed_hashes)) == 1,
            "dynamic_history_changes_output": len(set(dynamic_hashes)) >= 5,
            "downsample_contract": bool(node.downsample_records)
            and all(
                item.get("width") == 64
                and item.get("height") == 36
                and item.get("finite")
                and 0.0 <= item.get("min", -1.0) <= item.get("max", 9.0) <= 2.5 + 1.0e-5
                for item in node.downsample_records
            ),
            "crop_contract": bool(node.crop_records)
            and all(
                item.get("width") == 32
                and item.get("height") == 18
                and item.get("finite")
                and 0.0 <= item.get("min", -1.0) <= item.get("max", 9.0) <= 1.0 + 1.0e-5
                for item in node.crop_records
            ),
        }
        payload.update(
            {
                "reset_message": reset_response.message,
                "counts": {
                    "all_outputs": len(node.obs_records),
                    "fixed_outputs": len(node.fixed_outputs),
                    "dynamic_outputs": len(node.dynamic_outputs),
                    "fixed_unique": len(set(fixed_hashes)),
                    "dynamic_unique": len(set(dynamic_hashes)),
                },
                "timing": {
                    "elapsed_seconds": time.monotonic() - started,
                    "median_output_interval_ms": statistics.median(intervals) * 1000.0 if intervals else None,
                    "p95_output_interval_ms": sorted(intervals)[max(0, math.ceil(0.95 * len(intervals)) - 1)] * 1000.0
                    if intervals
                    else None,
                },
                "first_fixed_output": {
                    "values": node.fixed_outputs[0] if node.fixed_outputs else [],
                    "sha256_float32_le": fixed_hashes[0] if fixed_hashes else None,
                },
                "downsample_samples": node.downsample_records,
                "crop_samples": node.crop_records,
                "checks": checks,
                "status": "PASS" if all(checks.values()) else "FAIL",
            }
        )
    except Exception as error:
        payload["error"] = f"{type(error).__name__}: {error}"
        payload["timing"] = {"elapsed_seconds": time.monotonic() - started}
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
