#!/usr/bin/env python3
"""Run the official PCD-to-PGM algorithm without starting ROS or devices."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import pathlib
import sys
import time
from typing import Any

import numpy as np
from PIL import Image


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_official_module(path: pathlib.Path) -> Any:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("roboto_official_pcd2pgm", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load official converter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_simple_map_yaml(path: pathlib.Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"invalid map YAML line: {raw_line}")
        value = value.strip()
        if value.startswith("["):
            values[key] = ast.literal_eval(value)
        elif key in {"resolution", "occupied_thresh", "free_thresh"}:
            values[key] = float(value)
        elif key == "negate":
            values[key] = int(value)
        else:
            values[key] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-script", required=True, type=pathlib.Path)
    parser.add_argument("--input-pcd", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--summary", required=True, type=pathlib.Path)
    parser.add_argument("--map-name", default="map_ikdtree_x5_offline")
    args = parser.parse_args()

    official_script = args.official_script.resolve(strict=True)
    input_pcd = args.input_pcd.resolve(strict=True)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.summary.resolve()
    if summary_path.parent != output_dir:
        raise ValueError("summary must be written directly inside output-dir")

    parameters = {
        "resolution": 0.05,
        "elevation_resolution": 0.05,
        "ground_percentile": 100.0,
        "robot_height": 1.5,
        "clearance_margin": 0.2,
        "max_step_height": 0.035,
        "max_slope_angle": 25.0,
        "smooth_kernel": 3,
        "min_point_density": 1,
        "occupied_thresh": 0.65,
        "free_thresh": 0.196,
        "inflate_pixels": 0,
        "z_min": -0.1,
        "z_max": 1.9,
        "fill_unknown": True,
        "save_debug_pcd": False,
        "save_elevation_visualization": False,
    }

    started = time.perf_counter()
    converter = load_official_module(official_script)
    points = converter.load_pcd(
        str(input_pcd), z_min=parameters["z_min"], z_max=parameters["z_max"]
    )
    if points.ndim != 2 or points.shape[1] != 3 or points.shape[0] == 0:
        raise RuntimeError(f"unexpected filtered point shape: {points.shape}")
    if not np.isfinite(points).all():
        raise RuntimeError("filtered point cloud contains NaN or infinity")

    elevation, obstacles, point_counts, origin_x, origin_y, width, height = (
        converter.build_elevation_map(
            points,
            parameters["elevation_resolution"],
            parameters["robot_height"],
            parameters["clearance_margin"],
            parameters["ground_percentile"],
            parameters["smooth_kernel"],
        )
    )
    traversability = converter.compute_traversability(
        elevation,
        obstacles,
        point_counts,
        parameters["elevation_resolution"],
        parameters["max_step_height"],
        parameters["max_slope_angle"],
        parameters["min_point_density"],
    )
    traversability, elevation, origin_x, origin_y, width, height = (
        converter.downsample_traversability(
            traversability,
            elevation,
            parameters["elevation_resolution"],
            parameters["resolution"],
            origin_x,
            origin_y,
        )
    )
    if parameters["fill_unknown"]:
        if converter.binary_dilation is None:
            raise RuntimeError("SciPy is unavailable; cannot reproduce fill_unknown stage")
        traversability = converter.fill_unknown_regions(traversability, elevation)
    traversability = converter.inflate_obstacles(
        traversability, parameters["inflate_pixels"]
    )

    pgm_path = output_dir / f"{args.map_name}.pgm"
    yaml_path = output_dir / f"{args.map_name}.yaml"
    converter.traversability_to_pgm(traversability, str(pgm_path))
    converter.save_yaml(
        str(yaml_path),
        pgm_path.name,
        parameters["resolution"],
        origin_x,
        origin_y,
        parameters["occupied_thresh"],
        parameters["free_thresh"],
    )

    with Image.open(pgm_path) as image:
        image.load()
        pgm_array = np.asarray(image)
        image_mode = image.mode
        image_size = list(image.size)
    if image_mode != "L" or image_size != [width, height]:
        raise RuntimeError(
            f"unexpected PGM metadata: mode={image_mode} size={image_size} "
            f"expected={[width, height]}"
        )
    unique_values, counts = np.unique(pgm_array, return_counts=True)
    pixel_counts = {
        str(int(value)): int(count) for value, count in zip(unique_values, counts)
    }
    if not set(pixel_counts).issubset({"0", "205", "254"}):
        raise RuntimeError(f"unexpected PGM grayscale values: {pixel_counts}")
    if pixel_counts.get("254", 0) == 0:
        raise RuntimeError("PGM contains no free cells")

    yaml_values = parse_simple_map_yaml(yaml_path)
    expected_yaml = {
        "image": pgm_path.name,
        "mode": "trinary",
        "resolution": parameters["resolution"],
        "negate": 0,
        "occupied_thresh": parameters["occupied_thresh"],
        "free_thresh": parameters["free_thresh"],
    }
    for key, expected in expected_yaml.items():
        if yaml_values.get(key) != expected:
            raise RuntimeError(
                f"map YAML mismatch for {key}: {yaml_values.get(key)!r} != {expected!r}"
            )
    origin = yaml_values.get("origin")
    if (
        not isinstance(origin, list)
        or len(origin) != 3
        or not all(math.isfinite(float(value)) for value in origin)
    ):
        raise RuntimeError(f"invalid map origin: {origin!r}")

    cell_counts = {
        "free": int(np.sum(traversability == 0)),
        "occupied": int(np.sum(traversability == 1)),
        "unknown": int(np.sum(traversability == 2)),
    }
    if sum(cell_counts.values()) != width * height:
        raise RuntimeError("traversability counts do not cover the output grid")

    report = {
        "schema_version": 1,
        "scope": "official PCD-to-PGM algorithm on existing data; no ROS nodes or devices",
        "result": "PASS",
        "device_access": False,
        "ros_nodes_started": False,
        "official_converter": {
            "path": str(official_script),
            "sha256": sha256_file(official_script),
        },
        "input": {
            "path": str(input_pcd),
            "bytes": input_pcd.stat().st_size,
            "sha256": sha256_file(input_pcd),
            "filtered_point_count": int(points.shape[0]),
        },
        "parameters": parameters,
        "output": {
            "width": int(width),
            "height": int(height),
            "resolution": parameters["resolution"],
            "origin": [float(origin_x), float(origin_y), 0.0],
            "cell_counts": cell_counts,
            "pgm_pixel_counts": pixel_counts,
            "pgm": {
                "path": str(pgm_path),
                "bytes": pgm_path.stat().st_size,
                "sha256": sha256_file(pgm_path),
            },
            "yaml": {
                "path": str(yaml_path),
                "bytes": yaml_path.stat().st_size,
                "sha256": sha256_file(yaml_path),
            },
        },
        "elapsed_seconds": time.perf_counter() - started,
    }
    summary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"result=PASS points={points.shape[0]} grid={width}x{height} "
        f"elapsed_seconds={report['elapsed_seconds']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
