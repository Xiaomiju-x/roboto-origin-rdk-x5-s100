#!/usr/bin/env python3
"""Static audit of installed official navigation launch/config assets."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import re
from typing import Any

import yaml


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_file(
    path: pathlib.Path, kind: str, root: pathlib.Path
) -> dict[str, Any]:
    return {
        "relative_path": str(path.relative_to(root)),
        "kind": kind,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    install_root = args.install_root.resolve(strict=True)
    adapter_share = (
        install_root
        / "nav2_localization_adapter/share/nav2_localization_adapter"
    )
    localization_share = install_root / "robots_localization/share/robots_localization"
    expected_launch = [
        adapter_share / "launch/adapter.launch.py",
        adapter_share / "launch/navigation.launch.py",
        adapter_share / "launch/waypoint_navigation.launch.py",
        localization_share / "launch/localization.launch.py",
    ]
    expected_yaml = [
        adapter_share / "config/adapter_params.yaml",
        adapter_share / "config/nav2_params_dwb_garden.yaml",
        adapter_share / "config/nav2_params_mppi_garden.yaml",
        adapter_share / "config/nav2_params_mppi_room.yaml",
        adapter_share / "config/nav2_params_terrain.yaml",
        adapter_share / "config/waypoints.yaml",
        adapter_share / "map/garden.yaml",
        localization_share / "config/avia.yaml",
        localization_share / "config/mid360.yaml",
        localization_share / "config/ouster64.yaml",
        localization_share / "config/rse1r.yaml",
        localization_share / "config/unitreel2.yaml",
    ]
    expected_data = [
        adapter_share / "map/garden.pgm",
        localization_share / "PCD/map_ikdtree.pcd",
        localization_share / "PCD/garden_ikdtree.pcd",
        install_root
        / "nav2_localization_adapter/lib/nav2_localization_adapter/nav2_localization_adapter_node",
        install_root
        / "robots_localization/lib/robots_localization/robots_localization_node",
    ]

    failures: list[str] = []
    warnings: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    parsed_yaml: dict[pathlib.Path, Any] = {}

    for path in expected_launch:
        if not path.is_file():
            failures.append(f"missing launch file: {path}")
            continue
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(path))
        except SyntaxError as error:
            failures.append(f"launch syntax error: {path}: {error}")
        files.append(record_file(path, "python_launch", install_root))

    absolute_home_pattern = re.compile(r"/home/[^/\s\"']+(?:/[^\s\"']+)+")
    for path in expected_yaml:
        if not path.is_file():
            failures.append(f"missing YAML file: {path}")
            continue
        source = path.read_text(encoding="utf-8")
        try:
            parsed_yaml[path] = yaml.safe_load(source)
        except yaml.YAMLError as error:
            failures.append(f"YAML parse error: {path}: {error}")
        for line_number, line in enumerate(source.splitlines(), start=1):
            for match in absolute_home_pattern.findall(line):
                warnings.append(
                    {
                        "code": "NON_PORTABLE_ABSOLUTE_PATH",
                        "relative_path": str(path.relative_to(install_root)),
                        "line": line_number,
                        "value": match,
                    }
                )
        files.append(record_file(path, "yaml", install_root))

    for path in expected_data:
        if not path.is_file():
            failures.append(f"missing installed asset: {path}")
            continue
        if path.name.endswith("_node") and not (path.stat().st_mode & 0o111):
            failures.append(f"installed node is not executable: {path}")
        files.append(record_file(path, "data_or_executable", install_root))

    map_yaml_path = adapter_share / "map/garden.yaml"
    map_config = parsed_yaml.get(map_yaml_path)
    if isinstance(map_config, dict):
        image_name = map_config.get("image")
        image_path = map_yaml_path.parent / str(image_name)
        if not image_path.is_file():
            failures.append(f"map YAML image is missing: {image_path}")
        if map_config.get("mode") != "trinary":
            failures.append("installed garden map is not trinary")
    elif map_yaml_path.is_file():
        failures.append("installed garden map YAML is not a mapping")

    adapter_config_path = adapter_share / "config/adapter_params.yaml"
    adapter_config = parsed_yaml.get(adapter_config_path)
    contract: dict[str, Any] = {}
    try:
        parameters = adapter_config["nav2_localization_adapter"]["ros__parameters"]
        contract = {
            "odom_topic": parameters["odom_topic"],
            "map_frame": parameters["map_frame"],
            "odom_frame": parameters["odom_frame"],
            "base_frame": parameters["base_frame"],
            "tf_rate_hz": parameters["tf_rate_hz"],
        }
        expected_contract = {
            "odom_topic": "robot_0/odometry",
            "map_frame": "map",
            "odom_frame": "odom",
            "base_frame": "base_link",
            "tf_rate_hz": 10.0,
        }
        if contract != expected_contract:
            failures.append(
                f"adapter contract mismatch: {contract!r} != {expected_contract!r}"
            )
    except (KeyError, TypeError) as error:
        failures.append(f"adapter parameter structure is invalid: {error}")

    report = {
        "schema_version": 1,
        "scope": "static installed-asset audit only; launch files were parsed but not executed",
        "device_access": False,
        "ros_nodes_started": False,
        "result": "PASS" if not failures else "FAIL",
        "counts": {
            "launch_files": len(expected_launch),
            "yaml_files": len(expected_yaml),
            "data_and_executables": len(expected_data),
            "warnings": len(warnings),
            "failures": len(failures),
        },
        "adapter_contract": contract,
        "warnings": warnings,
        "failures": failures,
        "files": files,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"result={report['result']} launch={len(expected_launch)} "
        f"yaml={len(expected_yaml)} warnings={len(warnings)} failures={len(failures)}"
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
