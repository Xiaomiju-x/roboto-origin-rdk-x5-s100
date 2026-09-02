#!/usr/bin/env python3
"""Plan 20 deterministic Nav2 goals and capture mock cmd_vel only to a file."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import rclpy
from action_msgs.msg import GoalStatus
from nav2_msgs.action import ComputePathToPose

from navigation_plan_offline import (
    OfflinePlannerProbe,
    _eroded_free_mask,
    _farthest,
    _largest_component,
    _pose,
    _spin_until,
    _wait_for_active_planner,
    _world_xy,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--goal-count", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    if args.goal_count < 20:
        raise ValueError("D5 requires at least 20 goals")

    started = time.time()
    result: dict[str, object] = {
        "schema_version": 1,
        "status": "FAIL",
        "scope": "Nav2 planning with synthetic odometry and file-only velocity capture",
        "requested_control_topic": "/cmd_vel",
        "effective_capture_sink": str(args.capture),
        "capture_is_not_a_ros_publisher": True,
        "external_device_access": False,
        "control_output": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.capture.parent.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = OfflinePlannerProbe()
    capture_stream = args.capture.open("w", encoding="utf-8")
    try:
        publishers_before = len(node.get_publishers_info_by_topic("/cmd_vel"))
        _spin_until(node, lambda: node.map_message is not None, args.timeout, "transient-local map")
        grid = node.map_message
        assert grid is not None
        width = int(grid.info.width)
        height = int(grid.info.height)

        component: list[int] = []
        clearance = -1
        for radius in (4, 3, 2, 1, 0):
            candidate = _largest_component(_eroded_free_mask(grid, radius), width, height)
            if len(candidate) >= args.goal_count * 2:
                component = candidate
                clearance = radius
                break
        if len(component) < args.goal_count * 2:
            raise RuntimeError("map lacks a sufficiently large connected free-space component")

        allowed = bytearray(width * height)
        for index in component:
            allowed[index] = 1
        stride = max(1, len(component) // args.goal_count)
        pairs: list[tuple[int, int, int]] = []
        seen: set[tuple[int, int]] = set()
        for goal_index in range(args.goal_count):
            start_index = component[(17 + goal_index * stride) % len(component)]
            goal_cell, graph_distance = _farthest(start_index, allowed, width, height)
            pair = (start_index, goal_cell)
            if pair in seen:
                start_index = component[(97 + goal_index * 7919) % len(component)]
                goal_cell, graph_distance = _farthest(start_index, allowed, width, height)
                pair = (start_index, goal_cell)
            seen.add(pair)
            pairs.append((start_index, goal_cell, graph_distance))

        first_start = _world_xy(grid, pairs[0][0])
        node.current_xy = first_start
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

        goals: list[dict[str, object]] = []
        for goal_index, (start_cell, goal_cell, graph_distance) in enumerate(pairs):
            start_xy = _world_xy(grid, start_cell)
            goal_xy = _world_xy(grid, goal_cell)
            node.current_xy = start_xy
            for _ in range(3):
                rclpy.spin_once(node, timeout_sec=0.03)

            goal_message = ComputePathToPose.Goal()
            goal_message.start = _pose(node, start_xy)
            goal_message.goal = _pose(node, goal_xy)
            goal_message.planner_id = "GridBased"
            goal_message.use_start = True
            send_future = node.action_client.send_goal_async(goal_message)
            _spin_until(node, send_future.done, args.timeout, f"goal {goal_index} response")
            goal_handle = send_future.result()
            if goal_handle is None or not goal_handle.accepted:
                raise RuntimeError(f"planner rejected goal {goal_index}")
            result_future = goal_handle.get_result_async()
            _spin_until(node, result_future.done, args.timeout, f"goal {goal_index} result")
            wrapped = result_future.result()
            if wrapped is None or wrapped.status != GoalStatus.STATUS_SUCCEEDED:
                status = None if wrapped is None else wrapped.status
                raise RuntimeError(f"goal {goal_index} action status={status}")
            path = wrapped.result.path
            path_xy = [(pose.pose.position.x, pose.pose.position.y) for pose in path.poses]
            if len(path_xy) < 2 or not all(math.isfinite(v) for point in path_xy for v in point):
                raise RuntimeError(f"goal {goal_index} returned an invalid path")
            path_length = sum(math.dist(left, right) for left, right in zip(path_xy, path_xy[1:]))

            delta_x = path_xy[1][0] - path_xy[0][0]
            delta_y = path_xy[1][1] - path_xy[0][1]
            segment = max(math.hypot(delta_x, delta_y), 1.0e-9)
            captured = {
                "goal_index": goal_index,
                "logical_source": "/cmd_vel",
                "effective_sink": "file_only",
                "linear_x": min(0.2, segment * 10.0),
                "linear_y": 0.0,
                "angular_z": max(-0.5, min(0.5, math.atan2(delta_y, delta_x))),
                "published": False,
            }
            capture_stream.write(json.dumps(captured, sort_keys=True) + "\n")
            capture_stream.flush()
            goals.append(
                {
                    "index": goal_index,
                    "start_xy_m": list(start_xy),
                    "goal_xy_m": list(goal_xy),
                    "graph_distance_cells": graph_distance,
                    "path_pose_count": len(path_xy),
                    "path_length_m": path_length,
                    "finite": True,
                    "status": "PASS",
                }
            )

        capture_stream.close()
        publishers_after = len(node.get_publishers_info_by_topic("/cmd_vel"))
        checks = {
            "goal_count_gte_20": len(goals) >= 20,
            "all_goals_pass": len(goals) == args.goal_count
            and all(item["status"] == "PASS" for item in goals),
            "all_paths_finite": all(item["finite"] for item in goals),
            "capture_line_count_matches": sum(1 for _ in args.capture.open("r", encoding="utf-8"))
            == len(goals),
            "cmd_vel_publishers_before_zero": publishers_before == 0,
            "cmd_vel_publishers_after_zero": publishers_after == 0,
        }
        result.update(
            {
                "status": "PASS" if all(checks.values()) else "FAIL",
                "map": {
                    "width": width,
                    "height": height,
                    "resolution": float(grid.info.resolution),
                    "component_cells": len(component),
                    "clearance_radius_cells": clearance,
                },
                "adapter": {
                    "pose_messages_seen": node.pose_messages,
                    "tf_pairs_seen": sorted([list(pair) for pair in node.tf_pairs]),
                },
                "goals": goals,
                "goal_summary": {
                    "requested": args.goal_count,
                    "passed": len(goals),
                    "total_path_poses": sum(int(item["path_pose_count"]) for item in goals),
                    "total_path_length_m": sum(float(item["path_length_m"]) for item in goals),
                },
                "capture": {
                    "path": str(args.capture),
                    "sha256": sha256(args.capture),
                    "lines": len(goals),
                },
                "cmd_vel_publishers": {"before": publishers_before, "after": publishers_after},
                "checks": checks,
            }
        )
    except Exception as error:  # preserve evidence on failure
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        if not capture_stream.closed:
            capture_stream.close()
        result["elapsed_seconds"] = time.time() - started
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        node.destroy_node()
        rclpy.shutdown()

    print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
