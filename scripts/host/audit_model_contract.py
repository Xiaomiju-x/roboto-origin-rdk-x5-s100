#!/usr/bin/env python3
"""Static RPO model/deployment contract audit.

This script parses source files only. It does not import ROS, Isaac Lab, MuJoCo,
ONNX Runtime, or any robot driver, and it never opens a hardware device.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Joint:
    name: str
    axis: list[float] | None
    lower: float | None
    upper: float | None


@dataclass
class Check:
    status: str
    name: str
    detail: str


class Audit:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, condition: bool, name: str, ok: str, bad: str) -> None:
        self.checks.append(Check("PASS" if condition else "FAIL", name, ok if condition else bad))

    def warn(self, name: str, detail: str) -> None:
        self.checks.append(Check("WARN", name, detail))

    @property
    def failed(self) -> bool:
        return any(item.status == "FAIL" for item in self.checks)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def floats(value: str | None) -> list[float] | None:
    if value is None:
        return None
    return [float(item) for item in value.split()]


def urdf_joints(path: Path) -> list[Joint]:
    root = ET.parse(path).getroot()
    result: list[Joint] = []
    for element in root.findall("joint"):
        joint_type = element.get("type", "")
        if joint_type == "fixed":
            continue
        name = element.get("name")
        if not name:
            continue
        axis_node = element.find("axis")
        limit_node = element.find("limit")
        result.append(
            Joint(
                name=name,
                axis=floats(axis_node.get("xyz") if axis_node is not None else None),
                lower=float(limit_node.get("lower")) if limit_node is not None and limit_node.get("lower") else None,
                upper=float(limit_node.get("upper")) if limit_node is not None and limit_node.get("upper") else None,
            )
        )
    return result


def mjcf_joints(path: Path) -> list[Joint]:
    root = ET.parse(path).getroot()
    result: list[Joint] = []
    for element in root.iter("joint"):
        name = element.get("name")
        if not name or element.get("type") == "free":
            continue
        limits = floats(element.get("range"))
        result.append(
            Joint(
                name=name,
                axis=floats(element.get("axis")),
                lower=limits[0] if limits and len(limits) == 2 else None,
                upper=limits[1] if limits and len(limits) == 2 else None,
            )
        )
    return result


def mjcf_actuators(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    actuator = root.find("actuator")
    if actuator is None:
        return []
    return [item.get("joint") for item in actuator if item.get("joint")]


def yaml_list(path: Path, key: str, occurrence: int = 0) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(
        rf"(?ms)^[ \t]*{re.escape(key)}:[ \t]*(?:\r?\n[ \t]*)?\[(.*?)\]",
        text,
    )
    if occurrence >= len(matches):
        raise ValueError(f"missing YAML list {key!r} occurrence {occurrence} in {path}")
    return list(ast.literal_eval("[" + matches[occurrence] + "]"))


def yaml_scalar(path: Path, key: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"(?m)^[ \t]*{re.escape(key)}:[ \t]*([^#\r\n]+)", text)
    if not match:
        raise ValueError(f"missing YAML scalar {key!r} in {path}")
    return match.group(1).strip().strip('"\'')


def names(joints: list[Joint]) -> list[str]:
    return [item.name for item in joints]


def limit_differences(reference: list[Joint], candidate: list[Joint], tolerance: float = 1e-6) -> list[str]:
    reference_map = {item.name: item for item in reference}
    differences: list[str] = []
    for item in candidate:
        expected = reference_map.get(item.name)
        if expected is None:
            continue
        if item.lower is None or item.upper is None or expected.lower is None or expected.upper is None:
            continue
        if not math.isclose(item.lower, expected.lower, abs_tol=tolerance) or not math.isclose(
            item.upper, expected.upper, abs_tol=tolerance
        ):
            differences.append(
                f"{item.name}: candidate=[{item.lower:g},{item.upper:g}] reference=[{expected.lower:g},{expected.upper:g}]"
            )
    return differences


def policy_limit_overruns(reference: list[Joint], flat_limits: list[float], tolerance: float = 1e-6) -> list[str]:
    overruns: list[str] = []
    for index, joint in enumerate(reference):
        lower = float(flat_limits[index * 2])
        upper = float(flat_limits[index * 2 + 1])
        if joint.lower is None or joint.upper is None:
            continue
        if lower < joint.lower - tolerance or upper > joint.upper + tolerance:
            overruns.append(
                f"{joint.name}: deploy=[{lower:g},{upper:g}] urdf=[{joint.lower:g},{joint.upper:g}]"
            )
    return overruns


def audit(project_root: Path) -> tuple[Audit, dict[str, Any]]:
    upstream = project_root / "upstream"
    canonical_urdf_path = upstream / "rpo_description" / "urdf" / "rpo.urdf"
    canonical_mjcf_path = upstream / "rpo_description" / "mjcf" / "rpo.xml"
    train_rpo = upstream / "roboparty_train" / "robolab" / "data" / "robots" / "roboparty" / "rpo"
    train_urdf_path = train_rpo / "urdf" / "rpo.urdf"
    train_mjcf_path = train_rpo / "mjcf" / "rpo.xml"
    gmr_root = upstream / "GMR"
    gmr_urdf_path = gmr_root / "assets" / "rpo" / "rpo.urdf"
    gmr_mjcf_path = gmr_root / "assets" / "rpo" / "rpo.xml"
    gmr_pose_path = gmr_root / "ik_config_manager" / "pose_inits" / "rpo.json"
    deploy_root = upstream / "roboparty_deploy"
    robot_yaml = deploy_root / "src" / "inference" / "robots" / "rpo" / "robot.yaml"
    motion_yaml = deploy_root / "scripts" / "config" / "motion_player.yaml"
    set_zero_yaml = deploy_root / "scripts" / "config" / "set_zero.yaml"
    policy_dir = deploy_root / "src" / "inference" / "robots" / "rpo" / "configs"

    required = [
        canonical_urdf_path,
        canonical_mjcf_path,
        train_urdf_path,
        train_mjcf_path,
        gmr_urdf_path,
        gmr_mjcf_path,
        gmr_pose_path,
        robot_yaml,
        motion_yaml,
        set_zero_yaml,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("required files missing:\n" + "\n".join(missing))

    result = Audit()
    canonical_urdf = urdf_joints(canonical_urdf_path)
    canonical_mjcf = mjcf_joints(canonical_mjcf_path)
    canonical_actuators = mjcf_actuators(canonical_mjcf_path)
    canonical_names = names(canonical_urdf)

    result.add(
        len(canonical_names) == 23 and len(set(canonical_names)) == 23,
        "canonical_dof_count",
        "canonical URDF has 23 unique non-fixed joints",
        f"canonical URDF count={len(canonical_names)} unique={len(set(canonical_names))}",
    )
    result.add(
        names(canonical_mjcf) == canonical_names,
        "canonical_urdf_mjcf_order",
        "canonical URDF and MJCF joint orders match",
        "canonical URDF and MJCF joint orders differ",
    )
    result.add(
        canonical_actuators == canonical_names,
        "canonical_actuator_order",
        "MJCF actuator order matches canonical joint order",
        "MJCF actuator order differs from canonical joint order",
    )
    canonical_limit_diff = limit_differences(canonical_urdf, canonical_mjcf)
    result.add(
        not canonical_limit_diff,
        "canonical_joint_limits",
        "canonical URDF and MJCF limits match",
        "; ".join(canonical_limit_diff),
    )

    result.add(
        sha256(train_urdf_path) == sha256(canonical_urdf_path),
        "train_urdf_pin",
        "training URDF is byte-identical to canonical URDF",
        "training URDF differs from canonical URDF",
    )
    result.add(
        sha256(train_mjcf_path) == sha256(canonical_mjcf_path),
        "train_mjcf_pin",
        "training MJCF is byte-identical to canonical MJCF",
        "training MJCF differs from canonical MJCF",
    )

    gmr_urdf = urdf_joints(gmr_urdf_path)
    gmr_mjcf = mjcf_joints(gmr_mjcf_path)
    result.add(
        names(gmr_urdf) == canonical_names,
        "gmr_urdf_joint_order",
        "GMR URDF joint order matches canonical order",
        "GMR URDF joint order differs from canonical order",
    )
    result.add(
        names(gmr_mjcf) == canonical_names and mjcf_actuators(gmr_mjcf_path) == canonical_names,
        "gmr_mjcf_joint_order",
        "GMR MJCF joint and actuator orders match canonical order",
        "GMR MJCF joint or actuator order differs from canonical order",
    )
    gmr_limit_diff = limit_differences(canonical_urdf, gmr_urdf) + limit_differences(canonical_urdf, gmr_mjcf)
    result.add(
        not gmr_limit_diff,
        "gmr_joint_limits",
        "GMR URDF/MJCF limits match canonical limits",
        "; ".join(gmr_limit_diff),
    )
    if sha256(gmr_urdf_path) != sha256(canonical_urdf_path):
        result.warn("gmr_urdf_bytes", "GMR URDF bytes differ; semantic joint contract is checked separately")
    if sha256(gmr_mjcf_path) != sha256(canonical_mjcf_path):
        result.warn("gmr_mjcf_bytes", "GMR MJCF bytes differ; semantic joint contract is checked separately")

    gmr_pose = json.loads(gmr_pose_path.read_text(encoding="utf-8"))
    pose_names = list(gmr_pose.get("joints", {}).keys())
    result.add(
        len(pose_names) == 23 and set(pose_names) == set(canonical_names),
        "gmr_pose_joint_set",
        "GMR named pose covers all 23 canonical joints",
        f"GMR pose missing={sorted(set(canonical_names) - set(pose_names))} extra={sorted(set(pose_names) - set(canonical_names))}",
    )
    if pose_names != canonical_names:
        result.warn("gmr_pose_order", "GMR pose JSON order differs, but values are keyed by joint name")

    motor_ids = yaml_list(robot_yaml, "motor_id")
    motor_num = yaml_list(robot_yaml, "motor_num")
    motor_interfaces = yaml_list(robot_yaml, "motor_interface")
    motor_models = yaml_list(robot_yaml, "motor_model")
    motor_zero = yaml_list(robot_yaml, "motor_zero_offset")
    kp = yaml_list(robot_yaml, "kp")
    kd = yaml_list(robot_yaml, "kd")
    motor_sign = yaml_list(robot_yaml, "motor_sign")
    urdf2motor = yaml_list(robot_yaml, "urdf2motor")
    close_chain_idx = yaml_list(robot_yaml, "close_chain_motor_idx")

    result.add(
        motor_ids == list(range(1, 24)),
        "deploy_motor_ids",
        "runtime motor IDs are exactly 1..23",
        f"runtime motor IDs={motor_ids}",
    )
    result.add(
        motor_num == [6, 7, 5, 5] and sum(motor_num) == 23,
        "deploy_bus_partition",
        "runtime bus partition is [6,7,5,5] and sums to 23",
        f"runtime motor_num={motor_num} sum={sum(motor_num)}",
    )
    result.add(
        motor_interfaces == ["can0", "can1", "can2", "can3"],
        "deploy_can_interfaces",
        "runtime expects can0..can3",
        f"runtime motor_interface={motor_interfaces}",
    )
    vector_lengths = {
        "motor_model": len(motor_models),
        "motor_zero_offset": len(motor_zero),
        "kp": len(kp),
        "kd": len(kd),
        "motor_sign": len(motor_sign),
        "urdf2motor": len(urdf2motor),
    }
    result.add(
        all(length == 23 for length in vector_lengths.values()),
        "deploy_vector_lengths",
        "runtime motor/control vectors all have 23 entries",
        f"runtime vector lengths={vector_lengths}",
    )
    result.add(
        urdf2motor == list(range(23)),
        "deploy_urdf2motor",
        "runtime URDF-to-motor mapping is the identity permutation",
        f"runtime urdf2motor={urdf2motor}",
    )
    result.add(
        close_chain_idx == [4, 5, 10, 11],
        "deploy_close_chain_indices",
        "runtime close-chain indices are the expected zero-based [4,5,10,11]",
        f"runtime close_chain_motor_idx={close_chain_idx}",
    )

    motion_zero = yaml_list(motion_yaml, "motor_zero_offset")
    set_zero = yaml_list(set_zero_yaml, "motor_zero_offset")
    result.add(
        motor_zero == motion_zero,
        "runtime_motion_zero_offsets",
        "runtime and motion-player zero offsets match",
        f"runtime zero offsets differ from motion player at indices {[i for i, pair in enumerate(zip(motor_zero, motion_zero)) if pair[0] != pair[1]]}",
    )
    if motor_zero != set_zero:
        result.warn(
            "set_zero_offsets",
            "set_zero template differs from runtime calibration; never use it as a runtime offset source",
        )

    policy_summaries: dict[str, Any] = {}
    expected_usd2urdf: list[Any] | None = None
    for config_path in sorted(policy_dir.glob("*.yaml")):
        joint_num = int(yaml_scalar(config_path, "joint_num"))
        dt = float(yaml_scalar(config_path, "dt"))
        decimation = int(yaml_scalar(config_path, "decimation"))
        usd2urdf = yaml_list(config_path, "usd2urdf")
        default_angles = yaml_list(config_path, "joint_default_angle")
        limits = yaml_list(config_path, "joint_limits")
        overruns = policy_limit_overruns(canonical_urdf, limits) if len(limits) == 46 else ["invalid limit vector"]
        expected_usd2urdf = usd2urdf if expected_usd2urdf is None else expected_usd2urdf
        shape_ok = (
            joint_num == 23
            and len(default_angles) == 23
            and len(limits) == 46
            and sorted(usd2urdf) == list(range(23))
            and usd2urdf == expected_usd2urdf
        )
        result.add(
            shape_ok,
            f"policy_shape_{config_path.stem}",
            f"{config_path.name}: 23 joints, consistent USD mapping, valid angle/limit lengths",
            f"{config_path.name}: joint_num={joint_num}, default={len(default_angles)}, limits={len(limits)}, usd2urdf={usd2urdf}",
        )
        result.add(
            not overruns,
            f"policy_limits_{config_path.stem}",
            f"{config_path.name}: software limits stay within canonical URDF",
            f"{config_path.name}: software limits exceed canonical URDF for " + "; ".join(overruns),
        )
        policy_summaries[config_path.name] = {
            "joint_num": joint_num,
            "dt": dt,
            "decimation": decimation,
            "low_level_hz": 1.0 / dt,
            "policy_hz": 1.0 / (dt * decimation),
            "limit_overruns": overruns,
        }

    report = {
        "schema_version": 1,
        "scope": "static source contract only; no simulation, ROS graph, CAN, or motor access",
        "canonical_joint_order": canonical_names,
        "canonical_urdf_sha256": sha256(canonical_urdf_path),
        "canonical_mjcf_sha256": sha256(canonical_mjcf_path),
        "train_urdf_sha256": sha256(train_urdf_path),
        "train_mjcf_sha256": sha256(train_mjcf_path),
        "gmr_urdf_sha256": sha256(gmr_urdf_path),
        "gmr_mjcf_sha256": sha256(gmr_mjcf_path),
        "deploy": {
            "motor_ids": motor_ids,
            "motor_num": motor_num,
            "motor_interface": motor_interfaces,
            "motor_model": motor_models,
            "motor_zero_offset": motor_zero,
            "kp": kp,
            "kd": kd,
            "motor_sign": motor_sign,
            "urdf2motor": urdf2motor,
            "close_chain_motor_idx": close_chain_idx,
        },
        "policies": policy_summaries,
        "checks": [asdict(item) for item in result.checks],
        "result": "FAIL" if result.failed else "PASS",
    }
    return result, report


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RPO 模型与控制契约静态审计",
        "",
        f"结论：`{report['result']}`。此结果仅覆盖源码静态契约，不代表仿真或实机能力。",
        "",
        "## 检查结果",
        "",
        "| 状态 | 检查 | 说明 |",
        "| --- | --- | --- |",
    ]
    for item in report["checks"]:
        detail = str(item["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['status']} | `{item['name']}` | {detail} |")
    lines.extend(["", "## 规范关节顺序", ""])
    for index, name in enumerate(report["canonical_joint_order"]):
        lines.append(f"{index:02d}. `{name}`")
    lines.extend(
        [
            "",
            "## 频率契约",
            "",
            "所有部署策略均配置 `dt=0.004`、`decimation=5`，对应 250 Hz 低层步长和 50 Hz 策略频率。",
            "",
            "## 安全解释",
            "",
            "任一部署软件限位超出规范 URDF，或运行时零偏来源不一致，都必须在接触 CAN/执行器前解析。报告不授权运行 `set_zero`、电机驱动或策略节点。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    try:
        _, report = audit(args.project_root.resolve())
    except Exception as error:  # make audit setup failures explicit in CI/logs
        print(f"ERROR {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(payload, end="")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload, encoding="utf-8", newline="\n")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown(report), encoding="utf-8", newline="\n")
    return 1 if report["result"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
