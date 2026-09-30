"""Sensor-frame occupancy and reusable A* research preview; no actuator interface."""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "deps"))
from pathfinding.core.diagonal_movement import DiagonalMovement
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder


def intent(text):
    text = "".join(text.split()).strip("，。！？,.!?")
    for name, cid in [("椅子", 56), ("人", 0), ("瓶子", 39)]:
        if text in [f"找到前面的{name}", f"找到{name}", f"寻找{name}"]:
            return {
                "intent": "FIND_TARGET",
                "class_id": cid,
                "target": name,
                "motion_output": False,
            }
    return {"intent": "REJECTED", "motion_output": False}


def lidar_grid(points, radius=0.30, resolution=0.05, extent=3.0):
    n = int(2 * extent / resolution) + 1
    free = np.zeros((n, n), np.uint8)  # unknown remains blocked
    occupied = np.zeros_like(free)
    center = n // 2
    for a, d, q in points:
        if (
            not all(math.isfinite(float(v)) for v in [a, d, q])
            or not 0.03 <= d <= 12
            or q <= 0
        ):
            continue
        x = math.cos(math.radians(a)) * d
        y = math.sin(math.radians(a)) * d
        gx = int(round(center + x / resolution))
        gy = int(round(center - y / resolution))
        cv2.line(free, (center, center), (gx, gy), 1, 1)
        if 0 <= gx < n and 0 <= gy < n:
            occupied[gy, gx] = 1
    r = int(math.ceil(radius / resolution))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    inflated = cv2.dilate(occupied, kernel)
    free[inflated != 0] = 0
    return free, occupied, inflated


def plan(points, goal=(2.0, 0.0), radius=0.30):
    free, occupied, inflated = lidar_grid(points, radius)
    center = free.shape[0] // 2
    gx = int(round(center + goal[0] / 0.05))
    gy = int(round(center - goal[1] / 0.05))
    record = {
        "frame": "lidar_native_UNCALIBRATED",
        "assumed_radius_m": radius,
        "robot_footprint_validated": False,
        "motion_output": False,
        "path_m": [],
        "unknown_policy": "blocked",
        "goal_m": list(goal),
    }
    if not free[center, center]:
        record["status"] = "START_OCCUPIED_OR_UNKNOWN"
    elif not (0 <= gx < free.shape[1] and 0 <= gy < free.shape[0]) or not free[gy, gx]:
        record["status"] = "GOAL_OCCUPIED_OR_UNKNOWN"
    else:
        grid = Grid(matrix=free.tolist())
        finder = AStarFinder(diagonal_movement=DiagonalMovement.only_when_no_obstacle)
        t = time.perf_counter()
        path, runs = finder.find_path(
            grid.node(center, center), grid.node(gx, gy), grid
        )
        record.update(
            status="SENSOR_FRAME_CANDIDATE" if path else "NO_PATH",
            planner_ms=(time.perf_counter() - t) * 1000,
            expanded_nodes=runs,
            path_m=[[(p.x - center) * 0.05, (center - p.y) * 0.05] for p in path],
        )
    image = np.full((*free.shape, 3), 70, np.uint8)
    image[free > 0] = (235, 235, 235)
    image[inflated > 0] = (60, 80, 180)
    image[occupied > 0] = (0, 0, 0)
    for x, y in record["path_m"]:
        cv2.circle(
            image,
            (round(center + x / 0.05), round(center - y / 0.05)),
            1,
            (0, 220, 0),
            -1,
        )
    cv2.circle(image, (center, center), 2, (220, 180, 0), -1)
    return record, cv2.resize(image, (605, 605), interpolation=cv2.INTER_NEAREST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--vision-events", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--text", default="找到前面的椅子")
    ap.add_argument(
        "--seconds",
        type=int,
        default=0,
        help="tail live sensor events for a bounded duration",
    )
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    seen = 0
    start = time.monotonic()
    live_intents = []
    while True:
        data = args.events.read_text() if args.events.exists() else ""
        lines = data.split("\n")[:-1]  # do not parse a writer's incomplete last line
        for line in lines[seen:]:
            event = json.loads(line)
            if event.get("kind") == "scan":
                result, image = plan(event["points"])
                result["source_monotonic"] = event["monotonic"]
                results.append(result)
            if event.get("kind") == "intent" and event.get("intent") == "FIND_TARGET":
                live_intents.append(event)
        seen = len(lines)
        if not args.seconds or time.monotonic() - start >= args.seconds:
            break
        time.sleep(0.25)
    if results:
        cv2.imwrite(str(args.output / "occupancy.jpg"), image)
    command = intent(args.text)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    targets = []
    if (
        args.vision_events
        and args.vision_events.exists()
        and command["intent"] == "FIND_TARGET"
    ):
        for line in args.vision_events.read_text().split("\n")[:-1]:
            event = json.loads(line)
            for obj in event["objects"]:
                if obj["class_id"] == command["class_id"]:
                    targets.append(obj)
    summary = {
        "scans_processed": len(results),
        "planning_status_counts": counts,
        "text_fixture": command,
        "target_observations": len(targets),
        "latest_visual_target": targets[-1] if targets else None,
        "target_location_status": "RGB_DEPTH_REGISTRATION_AND_EXTRINSICS_PENDING",
        "goal_source": "fixed_2m_in_lidar_frame_not_visual_target",
        "live_asr_target_requests": live_intents,
        "geometric_fusion": False,
        "motion_output": False,
    }
    (args.output / "paths.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in results)
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
