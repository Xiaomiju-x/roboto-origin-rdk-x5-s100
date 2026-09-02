#!/usr/bin/env python3
"""Build a project-owned, explicitly non-deployable RPO joint contract candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audit_model_contract import mjcf_joints, sha256, urdf_joints, yaml_list


def limits_by_name(items: list[Any]) -> dict[str, list[float | None]]:
    return {item.name: [item.lower, item.upper] for item in items}


def bus_map(interfaces: list[str], counts: list[int]) -> list[str]:
    result: list[str] = []
    for interface, count in zip(interfaces, counts):
        result.extend([interface] * int(count))
    return result


def build(project_root: Path) -> dict[str, Any]:
    upstream = project_root / "upstream"
    description = upstream / "rpo_description"
    deploy = upstream / "roboparty_deploy"
    gmr = upstream / "GMR" / "assets" / "rpo"
    robot_yaml = deploy / "src" / "inference" / "robots" / "rpo" / "robot.yaml"
    motion_yaml = deploy / "scripts" / "config" / "motion_player.yaml"
    set_zero_yaml = deploy / "scripts" / "config" / "set_zero.yaml"
    paths = {
        "canonical_urdf": description / "urdf" / "rpo.urdf",
        "canonical_mjcf": description / "mjcf" / "rpo.xml",
        "gmr_urdf": gmr / "rpo.urdf",
        "gmr_mjcf": gmr / "rpo.xml",
    }
    source_paths = {
        **paths,
        "runtime_robot_yaml": robot_yaml,
        "motion_player_yaml": motion_yaml,
        "set_zero_yaml": set_zero_yaml,
        "x5_dependency_probe": project_root / "evidence" / "x5" / "phase_a" / "dependency_probe_20260828.txt",
    }
    parsed = {
        "canonical_urdf": urdf_joints(paths["canonical_urdf"]),
        "canonical_mjcf": mjcf_joints(paths["canonical_mjcf"]),
        "gmr_urdf": urdf_joints(paths["gmr_urdf"]),
        "gmr_mjcf": mjcf_joints(paths["gmr_mjcf"]),
    }
    limit_maps = {name: limits_by_name(items) for name, items in parsed.items()}
    canonical_names = [item.name for item in parsed["canonical_urdf"]]

    motor_ids = [int(value) for value in yaml_list(robot_yaml, "motor_id")]
    motor_counts = [int(value) for value in yaml_list(robot_yaml, "motor_num")]
    motor_interfaces = [str(value) for value in yaml_list(robot_yaml, "motor_interface")]
    interfaces = bus_map(motor_interfaces, motor_counts)
    motor_model = [int(value) for value in yaml_list(robot_yaml, "motor_model")]
    motor_sign = [float(value) for value in yaml_list(robot_yaml, "motor_sign")]
    kp = [float(value) for value in yaml_list(robot_yaml, "kp")]
    kd = [float(value) for value in yaml_list(robot_yaml, "kd")]
    runtime_zero = [float(value) for value in yaml_list(robot_yaml, "motor_zero_offset")]
    motion_zero = [float(value) for value in yaml_list(motion_yaml, "motor_zero_offset")]
    set_zero = [float(value) for value in yaml_list(set_zero_yaml, "motor_zero_offset")]

    joints: list[dict[str, Any]] = []
    conflict_count = 0
    zero_conflict_count = 0
    for index, name in enumerate(canonical_names):
        source_limits = {source: limits[name] for source, limits in limit_maps.items()}
        unique_limits = {tuple(pair) for pair in source_limits.values()}
        source_conflict = len(unique_limits) > 1
        conflict_count += int(source_conflict)
        lower = max(float(pair[0]) for pair in source_limits.values() if pair[0] is not None)
        upper = min(float(pair[1]) for pair in source_limits.values() if pair[1] is not None)
        zero_values = {
            "runtime_robot_yaml": runtime_zero[index],
            "motion_player_yaml": motion_zero[index],
            "set_zero_template": set_zero[index],
        }
        zero_conflict = len(set(zero_values.values())) > 1
        zero_conflict_count += int(zero_conflict)
        joints.append(
            {
                "index": index,
                "name": name,
                "motor_id": motor_ids[index],
                "official_interface": interfaces[index],
                "motor_model": motor_model[index],
                "motor_sign": motor_sign[index],
                "kp_reference_only": kp[index],
                "kd_reference_only": kd[index],
                "published_limits_rad": source_limits,
                "published_limit_conflict": source_conflict,
                "static_source_intersection_rad": [lower, upper],
                "intersection_valid": lower <= upper,
                "zero_offsets_rad": zero_values,
                "zero_offset_conflict": zero_conflict,
                "physical_validation": "NOT_RUN",
            }
        )

    return {
        "schema_version": 1,
        "artifact": "RPO X5 joint contract candidate",
        "deployable": False,
        "deployment_guard": "MUST_NOT_BE_LOADED_BY_DRIVERS_OR POLICY NODES",
        "allowed_use": ["static comparison", "offline tests", "physical evidence collection planning"],
        "scope": "project-owned synthesis of pinned public sources; no physical authority established",
        "joint_count": len(joints),
        "published_limit_conflict_count": conflict_count,
        "zero_offset_conflict_count": zero_conflict_count,
        "source_sha256": {name: sha256(path) for name, path in source_paths.items()},
        "official_bus_partition": {"interfaces": motor_interfaces, "counts": motor_counts},
        "observed_x5_bus_state": "only can0 enumerated and DOWN; official can1-can3 not observed",
        "unresolved_gates": [
            "mechanical joint ranges and hard stops not physically verified",
            "motor identity, firmware limits, sign, and zero calibration not verified",
            "torso runtime zero offset conflicts with motion/set-zero templates",
            "official four-interface CAN topology is not present",
            "emergency stop, support fixture, wiring, polarity, termination, IDs, and bitrate not verified",
        ],
        "intersection_note": "The intersection is a static diagnostic only, not a safe motion limit.",
        "joints": joints,
    }


def markdown(report: dict[str, Any]) -> str:
    conflicts = [joint for joint in report["joints"] if joint["published_limit_conflict"]]
    zero_conflicts = [joint for joint in report["joints"] if joint["zero_offset_conflict"]]
    lines = [
        "# RPO X5 joint contract candidate",
        "",
        "Status: **NON-DEPLOYABLE / STATIC USE ONLY**",
        "",
        "This project-owned artifact does not choose a physical authority. Its source intersection is diagnostic, not a motion limit.",
        "",
        f"- Joints: {report['joint_count']}",
        f"- Published-limit conflicts: {report['published_limit_conflict_count']}",
        f"- Zero-offset conflicts: {report['zero_offset_conflict_count']}",
        "- Observed bus state: " + report["observed_x5_bus_state"],
        "",
        "## Limit conflicts",
        "",
        "| Joint | Static source intersection (rad) |",
        "| --- | --- |",
    ]
    for joint in conflicts:
        lower, upper = joint["static_source_intersection_rad"]
        lines.append(f"| {joint['name']} | [{lower:g}, {upper:g}] |")
    lines += ["", "## Zero-offset conflicts", ""]
    if zero_conflicts:
        for joint in zero_conflicts:
            lines.append(f"- `{joint['name']}`: {joint['zero_offsets_rad']}")
    else:
        lines.append("None in the compared source files.")
    lines += ["", "## Unresolved gates", ""]
    lines.extend(f"- {gate}" for gate in report["unresolved_gates"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()
    report = build(args.project_root.resolve())
    rendered_json = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered_json, encoding="utf-8", newline="\n")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown(report), encoding="utf-8", newline="\n")
    print(
        "RESULT NON_DEPLOYABLE "
        f"joints={report['joint_count']} limit_conflicts={report['published_limit_conflict_count']} "
        f"zero_conflicts={report['zero_offset_conflict_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
