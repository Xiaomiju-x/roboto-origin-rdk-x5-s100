#!/usr/bin/env python3

"""Exercise the official adapter and Nav2 planner without robot peripherals."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import deque
from pathlib import Path

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import ComputePathToPose
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from tf2_msgs.msg import TFMessage


def _stamp_to_float(stamp) -> float:
    return float(stamp.sec) + float(stamp.nanosec) * 1.0e-9


def _world_xy(grid: OccupancyGrid, index: int) -> tuple[float, float]:
    width = int(grid.info.width)
    row, col = divmod(index, width)
    resolution = float(grid.info.resolution)
    return (
        float(grid.info.origin.position.x) + (col + 0.5) * resolution,
        float(grid.info.origin.position.y) + (row + 0.5) * resolution,
    )


def _eroded_free_mask(grid: OccupancyGrid, radius: int) -> list[bool]:
    width = int(grid.info.width)
    height = int(grid.info.height)
    blocked = [1 if int(value) != 0 else 0 for value in grid.data]
    stride = width + 1
    prefix = [0] * ((height + 1) * stride)
    for row in range(height):
        row_sum = 0
        source_offset = row * width
        prefix_offset = (row + 1) * stride
        previous_offset = row * stride
        for col in range(width):
            row_sum += blocked[source_offset + col]
            prefix[prefix_offset + col + 1] = prefix[previous_offset + col + 1] + row_sum

    result = [False] * (width * height)
    for row in range(radius, height - radius):
        top = row - radius
        bottom = row + radius + 1
        for col in range(radius, width - radius):
            left = col - radius
            right = col + radius + 1
            total = (
                prefix[bottom * stride + right]
                - prefix[top * stride + right]
                - prefix[bottom * stride + left]
                + prefix[top * stride + left]
            )
            if total == 0:
                result[row * width + col] = True
    return result


def _neighbors(index: int, width: int, height: int):
    row, col = divmod(index, width)
    if col:
        yield index - 1
    if col + 1 < width:
        yield index + 1
    if row:
        yield index - width
    if row + 1 < height:
        yield index + width


def _largest_component(mask: list[bool], width: int, height: int) -> list[int]:
    visited = bytearray(len(mask))
    largest: list[int] = []
    for seed, is_free in enumerate(mask):
        if not is_free or visited[seed]:
            continue
        visited[seed] = 1
        queue = deque([seed])
        component: list[int] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in _neighbors(current, width, height):
                if mask[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
        if len(component) > len(largest):
            largest = component
    return largest


def _farthest(seed: int, allowed: bytearray, width: int, height: int) -> tuple[int, int]:
    distance = [-1] * len(allowed)
    distance[seed] = 0
    queue = deque([seed])
    farthest = seed
    while queue:
        current = queue.popleft()
        if distance[current] > distance[farthest]:
            farthest = current
        for neighbor in _neighbors(current, width, height):
            if allowed[neighbor] and distance[neighbor] < 0:
                distance[neighbor] = distance[current] + 1
                queue.append(neighbor)
    return farthest, distance[farthest]


def choose_endpoints(grid: OccupancyGrid) -> tuple[tuple[float, float], tuple[float, float], dict]:
    width = int(grid.info.width)
    height = int(grid.info.height)
    selected_component: list[int] = []
    selected_radius = -1
    for radius in (4, 3, 2, 1, 0):
        component = _largest_component(_eroded_free_mask(grid, radius), width, height)
        if len(component) >= 2:
            selected_component = component
            selected_radius = radius
            break
    if len(selected_component) < 2:
        raise RuntimeError("map has no connected free-space component with two cells")

    allowed = bytearray(width * height)
    for index in selected_component:
        allowed[index] = 1
    first, _ = _farthest(selected_component[0], allowed, width, height)
    second, graph_distance_cells = _farthest(first, allowed, width, height)
    start = _world_xy(grid, first)
    goal = _world_xy(grid, second)
    metadata = {
        "clearance_radius_cells": selected_radius,
        "component_cells": len(selected_component),
        "graph_distance_cells": graph_distance_cells,
        "straight_line_distance_m": math.dist(start, goal),
    }
    return start, goal, metadata


class OfflinePlannerProbe(Node):
    def __init__(self) -> None:
        super().__init__("roboto_offline_navigation_probe")
        map_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.map_message: OccupancyGrid | None = None
        self.pose_messages = 0
        self.tf_pairs: set[tuple[str, str]] = set()
        self.current_xy = (0.0, 0.0)
        self.odom_publisher = self.create_publisher(Odometry, "/robot_0/odometry", 10)
        self.create_subscription(OccupancyGrid, "/map", self._on_map, map_qos)
        self.create_subscription(PoseWithCovarianceStamped, "/pose", self._on_pose, 10)
        self.create_subscription(TFMessage, "/tf", self._on_tf, 100)
        self.action_client = ActionClient(self, ComputePathToPose, "/compute_path_to_pose")
        self.planner_state_client = self.create_client(GetState, "/planner_server/get_state")
        self.create_timer(0.05, self._publish_odom)

    def _on_map(self, message: OccupancyGrid) -> None:
        self.map_message = message

    def _on_pose(self, _message: PoseWithCovarianceStamped) -> None:
        self.pose_messages += 1

    def _on_tf(self, message: TFMessage) -> None:
        for transform in message.transforms:
            self.tf_pairs.add((transform.header.frame_id, transform.child_frame_id))

    def _publish_odom(self) -> None:
        message = Odometry()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "odom"
        message.child_frame_id = "base_link"
        message.pose.pose.position.x = self.current_xy[0]
        message.pose.pose.position.y = self.current_xy[1]
        message.pose.pose.orientation.w = 1.0
        self.odom_publisher.publish(message)


def _spin_until(node: Node, predicate, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        if predicate():
            return
    raise TimeoutError(f"timed out waiting for {label}")


def _pose(node: Node, xy: tuple[float, float]) -> PoseStamped:
    pose = PoseStamped()
    # A zero timestamp explicitly asks TF2 for the latest available transform.
    # This avoids turning launch sequencing into a false extrapolation failure.
    pose.header.frame_id = "map"
    pose.pose.position.x = xy[0]
    pose.pose.position.y = xy[1]
    pose.pose.orientation.w = 1.0
    return pose


def _wait_for_active_planner(node: OfflinePlannerProbe, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    if not node.planner_state_client.wait_for_service(timeout_sec=timeout_seconds):
        raise TimeoutError("planner lifecycle service unavailable")
    while time.monotonic() < deadline:
        future = node.planner_state_client.call_async(GetState.Request())
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        response = future.result() if future.done() else None
        if response is not None and response.current_state.id == State.PRIMARY_STATE_ACTIVE:
            return
        time.sleep(0.1)
    raise TimeoutError("planner_server did not reach ACTIVE lifecycle state")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    started = time.time()
    result = {
        "schema_version": 1,
        "status": "FAIL",
        "scope": "offline synthetic odometry; no robot or peripheral",
    }
    rclpy.init()
    node = OfflinePlannerProbe()
    try:
        _spin_until(node, lambda: node.map_message is not None, args.timeout, "transient-local map")
        grid = node.map_message
        assert grid is not None
        start, goal, selection = choose_endpoints(grid)
        node.current_xy = start

        _spin_until(
            node,
            lambda: node.pose_messages > 0
            and ("map", "odom") in node.tf_pairs
            and ("odom", "base_link") in node.tf_pairs,
            10.0,
            "official adapter pose and TF outputs",
        )
        _wait_for_active_planner(node, args.timeout)
        if not node.action_client.wait_for_server(timeout_sec=args.timeout):
            raise TimeoutError("compute_path_to_pose action server unavailable")

        goal_message = ComputePathToPose.Goal()
        goal_message.start = _pose(node, start)
        goal_message.goal = _pose(node, goal)
        goal_message.planner_id = "GridBased"
        goal_message.use_start = True

        send_future = node.action_client.send_goal_async(goal_message)
        _spin_until(node, send_future.done, args.timeout, "planner goal response")
        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            raise RuntimeError("planner rejected the goal")
        result_future = goal_handle.get_result_async()
        _spin_until(node, result_future.done, args.timeout, "planner result")
        wrapped_result = result_future.result()
        if wrapped_result is None:
            raise RuntimeError("planner returned no result")
        if wrapped_result.status != GoalStatus.STATUS_SUCCEEDED:
            raise RuntimeError(f"planner action status={wrapped_result.status}")
        path = wrapped_result.result.path
        if len(path.poses) < 2:
            raise RuntimeError(f"planner path has only {len(path.poses)} poses")

        path_xy = [(pose.pose.position.x, pose.pose.position.y) for pose in path.poses]
        if not all(math.isfinite(value) for point in path_xy for value in point):
            raise RuntimeError("planner path contains NaN/Inf")
        path_length = sum(math.dist(left, right) for left, right in zip(path_xy, path_xy[1:]))
        result.update(
            {
                "status": "PASS",
                "map": {
                    "frame_id": grid.header.frame_id,
                    "width": int(grid.info.width),
                    "height": int(grid.info.height),
                    "resolution": float(grid.info.resolution),
                    "stamp": _stamp_to_float(grid.header.stamp),
                    "free_cells": sum(1 for value in grid.data if int(value) == 0),
                    "occupied_cells": sum(1 for value in grid.data if int(value) >= 100),
                    "unknown_cells": sum(1 for value in grid.data if int(value) < 0),
                },
                "selection": selection,
                "start_xy_m": list(start),
                "goal_xy_m": list(goal),
                "path_pose_count": len(path.poses),
                "path_length_m": path_length,
                "adapter": {
                    "pose_messages_seen": node.pose_messages,
                    "tf_pairs_seen": sorted([list(pair) for pair in node.tf_pairs]),
                },
            }
        )
    except Exception as error:  # evidence must survive failures
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        result["elapsed_seconds"] = time.time() - started
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        node.destroy_node()
        rclpy.shutdown()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
