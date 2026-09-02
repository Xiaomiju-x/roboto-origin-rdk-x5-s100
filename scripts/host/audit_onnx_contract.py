#!/usr/bin/env python3
"""Dependency-free static ONNX/config contract audit.

The parser reads protobuf metadata directly. It never executes a model or
imports ONNX Runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from audit_model_contract import yaml_list, yaml_scalar


ELEMENT_TYPES = {
    1: "float32", 2: "uint8", 3: "int8", 4: "uint16", 5: "int16",
    6: "int32", 7: "int64", 8: "string", 9: "bool", 10: "float16",
    11: "float64", 12: "uint32", 13: "uint64", 16: "bfloat16",
}


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data) or shift >= 70:
            raise ValueError("invalid protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def fields(data: bytes) -> Iterable[tuple[int, int, int | bytes]]:
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number = tag >> 3
        wire = tag & 7
        if number == 0:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            value, offset = read_varint(data, offset)
            yield number, wire, value
        elif wire == 1:
            if offset + 8 > len(data):
                raise ValueError("truncated fixed64")
            yield number, wire, data[offset:offset + 8]
            offset += 8
        elif wire == 2:
            length, offset = read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError("truncated length-delimited field")
            yield number, wire, data[offset:end]
            offset = end
        elif wire == 5:
            if offset + 4 > len(data):
                raise ValueError("truncated fixed32")
            yield number, wire, data[offset:offset + 4]
            offset += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")


def first_field(data: bytes, number: int, wire: int | None = None) -> int | bytes | None:
    for field_number, field_wire, value in fields(data):
        if field_number == number and (wire is None or field_wire == wire):
            return value
    return None


def decode_text(value: int | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else ""


def tensor_shape(shape_proto: bytes) -> list[int | str | None]:
    shape: list[int | str | None] = []
    for number, wire, value in fields(shape_proto):
        if number != 1 or wire != 2 or not isinstance(value, bytes):
            continue
        dim_value = first_field(value, 1, 0)
        dim_param = first_field(value, 2, 2)
        if isinstance(dim_value, int):
            shape.append(dim_value)
        elif isinstance(dim_param, bytes):
            shape.append(decode_text(dim_param))
        else:
            shape.append(None)
    return shape


def value_info(value_proto: bytes) -> dict[str, Any]:
    name = decode_text(first_field(value_proto, 1, 2))
    type_proto = first_field(value_proto, 2, 2)
    element_type: int | None = None
    shape: list[int | str | None] = []
    if isinstance(type_proto, bytes):
        tensor_proto = first_field(type_proto, 1, 2)
        if isinstance(tensor_proto, bytes):
            raw_type = first_field(tensor_proto, 1, 0)
            element_type = raw_type if isinstance(raw_type, int) else None
            shape_proto = first_field(tensor_proto, 2, 2)
            if isinstance(shape_proto, bytes):
                shape = tensor_shape(shape_proto)
    return {
        "name": name,
        "element_type": ELEMENT_TYPES.get(element_type, str(element_type) if element_type else "unknown"),
        "shape": shape,
    }


def model_metadata(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    model_fields = list(fields(data))
    graph_proto = next(
        (value for number, wire, value in model_fields
         if number == 7 and wire == 2 and isinstance(value, bytes)),
        None,
    )
    if not isinstance(graph_proto, bytes):
        raise ValueError("ONNX graph missing")
    graph_fields = list(fields(graph_proto))
    initializer_names = {
        decode_text(first_field(value, 8, 2))
        for number, wire, value in graph_fields
        if number == 5 and wire == 2 and isinstance(value, bytes)
    }
    inputs = [
        value_info(value) for number, wire, value in graph_fields
        if number == 11 and wire == 2 and isinstance(value, bytes)
    ]
    inputs = [item for item in inputs if item["name"] not in initializer_names]
    outputs = [
        value_info(value) for number, wire, value in graph_fields
        if number == 12 and wire == 2 and isinstance(value, bytes)
    ]
    operators: Counter[str] = Counter()
    for number, wire, value in graph_fields:
        if number == 1 and wire == 2 and isinstance(value, bytes):
            operator = first_field(value, 4, 2)
            if isinstance(operator, bytes):
                operators[decode_text(operator)] += 1
    opsets = []
    for number, wire, value in model_fields:
        if number == 8 and wire == 2 and isinstance(value, bytes):
            version = first_field(value, 2, 0)
            opsets.append({
                "domain": decode_text(first_field(value, 1, 2)),
                "version": version if isinstance(version, int) else None,
            })
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ir_version": next(
            (value for number, wire, value in model_fields if number == 1 and wire == 0), None
        ),
        "producer_name": next(
            (decode_text(value) for number, wire, value in model_fields if number == 2 and wire == 2), ""
        ),
        "producer_version": next(
            (decode_text(value) for number, wire, value in model_fields if number == 3 and wire == 2), ""
        ),
        "opsets": opsets,
        "inputs": inputs,
        "outputs": outputs,
        "node_count": sum(operators.values()),
        "operators": dict(sorted(operators.items())),
    }


def yaml_dash_strings(path: Path, key: str) -> list[str]:
    source = path.read_text(encoding="utf-8")
    start = re.search(rf"(?m)^[ \t]*{re.escape(key)}:[ \t]*\r?$", source)
    if not start:
        raise ValueError(f"missing YAML block {key!r} in {path}")
    values: list[str] = []
    for line in source[start.end():].splitlines():
        match = re.match(r"^[ \t]+-[ \t]+[\"'](.*)[\"'][ \t]*$", line)
        if match:
            values.append(match.group(1))
        elif line.strip() and not line.lstrip().startswith("#"):
            break
    if not values:
        raise ValueError(f"empty YAML block {key!r} in {path}")
    return values


def stacked_layout_size(layout: str, frame_stack: int) -> int:
    total = 0
    for component in layout.split(","):
        match = re.search(r":([0-9]+)(?:@([0-9]+(?:\|[0-9]+)*))?\s*$", component.strip())
        if not match:
            raise ValueError(f"cannot parse observation component {component!r}")
        field_size = int(match.group(1))
        if match.group(2) is None:
            total += field_size * frame_stack
        else:
            taps = [int(item) for item in match.group(2).split("|")]
            if len(taps) != len(set(taps)) or any(item >= frame_stack for item in taps):
                raise ValueError(f"invalid sparse history taps in {component!r}")
            total += field_size * len(taps)
    return total


def shape_elements(shape: list[int | str | None]) -> int | None:
    if not shape or any(not isinstance(item, int) or item <= 0 for item in shape):
        return None
    return math.prod(int(item) for item in shape)


def audit(project_root: Path) -> dict[str, Any]:
    deploy = project_root / "upstream" / "roboparty_deploy"
    model_dir = deploy / "src" / "inference" / "robots" / "rpo" / "models"
    policy_dir = deploy / "src" / "inference" / "robots" / "rpo" / "configs"
    camera_model = deploy / "src" / "camera" / "models" / "encoder.onnx"
    camera_config = deploy / "src" / "camera" / "configs" / "parkour.yaml"

    expectations: dict[str, set[tuple[int, int]]] = {}
    references: dict[str, list[str]] = {}
    for config in sorted(policy_dir.glob("*.yaml")):
        model_names = [str(item) for item in yaml_list(config, "model_names")]
        layouts = yaml_dash_strings(config, "obs_layouts")
        stacks = [int(item) for item in yaml_list(config, "frame_stacks")]
        if not (len(model_names) == len(layouts) == len(stacks)):
            raise ValueError(f"model/layout/stack lengths differ in {config.name}")
        for model_name, layout, stack in zip(model_names, layouts, stacks):
            expectations.setdefault(model_name, set()).add((stacked_layout_size(layout, stack), 23))
            references.setdefault(model_name, []).append(config.name)

    crop_width = (
        int(yaml_scalar(camera_config, "depth_width"))
        - int(yaml_scalar(camera_config, "depth_crop_left"))
        - int(yaml_scalar(camera_config, "depth_crop_right"))
    )
    crop_height = (
        int(yaml_scalar(camera_config, "depth_height"))
        - int(yaml_scalar(camera_config, "depth_crop_up"))
        - int(yaml_scalar(camera_config, "depth_crop_down"))
    )
    depth_taps = yaml_list(camera_config, "depth_history_taps")
    encoder_output = int(yaml_scalar(camera_config, "depth_encoder_output_dim"))
    expectations["encoder.onnx"] = {(crop_width * crop_height * len(depth_taps), encoder_output)}
    references["encoder.onnx"] = ["camera/configs/parkour.yaml"]

    files = {path.name: path for path in model_dir.glob("*.onnx")}
    files[camera_model.name] = camera_model
    checks: list[dict[str, str]] = [{
        "status": "PASS" if set(files) == set(expectations) else "FAIL",
        "name": "model_inventory",
        "detail": f"files={sorted(files)} expected={sorted(expectations)}",
    }]
    models: dict[str, Any] = {}
    for model_name in sorted(expectations):
        path = files.get(model_name)
        if path is None:
            checks.append({"status": "FAIL", "name": "model_" + model_name, "detail": "file missing"})
            continue
        metadata = model_metadata(path)
        metadata["relative_path"] = str(path.relative_to(deploy)).replace("\\", "/")
        metadata["config_references"] = sorted(references[model_name])
        metadata["expected_contracts"] = [
            {"input_elements": item[0], "output_elements": item[1]}
            for item in sorted(expectations[model_name])
        ]
        models[model_name] = metadata
        if len(expectations[model_name]) != 1:
            checks.append({
                "status": "FAIL",
                "name": "expectation_" + model_name,
                "detail": f"conflicting contracts={sorted(expectations[model_name])}",
            })
            continue
        expected_input, expected_output = next(iter(expectations[model_name]))
        actual_inputs = [shape_elements(item["shape"]) for item in metadata["inputs"]]
        actual_outputs = [shape_elements(item["shape"]) for item in metadata["outputs"]]
        io_ok = (
            len(actual_inputs) == 1
            and len(actual_outputs) == 1
            and actual_inputs[0] == expected_input
            and actual_outputs[0] == expected_output
            and metadata["inputs"][0]["element_type"] == "float32"
            and metadata["outputs"][0]["element_type"] == "float32"
        )
        checks.append({
            "status": "PASS" if io_ok else "FAIL",
            "name": "io_" + model_name,
            "detail": (
                f"expected={expected_input}->{expected_output} "
                f"actual={actual_inputs}->{actual_outputs} "
                f"types={[item['element_type'] for item in metadata['inputs']]}->"
                f"{[item['element_type'] for item in metadata['outputs']]}"
            ),
        })

    return {
        "schema_version": 1,
        "scope": "static ONNX protobuf/config audit only; models were not executed",
        "result": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "camera_preprocess": {
            "crop_width": crop_width,
            "crop_height": crop_height,
            "history_taps": depth_taps,
            "encoder_input_elements": crop_width * crop_height * len(depth_taps),
            "encoder_output_elements": encoder_output,
        },
        "checks": checks,
        "models": models,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ONNX 与配置静态契约审计",
        "",
        f"结论：{report['result']}。模型仅被解析，未执行推理。",
        "",
        "| 状态 | 检查 | 说明 |",
        "| --- | --- | --- |",
    ]
    for item in report["checks"]:
        lines.append(f"| {item['status']} | {item['name']} | {item['detail'].replace('|', '/')} |")
    lines.extend([
        "",
        "## 模型接口",
        "",
        "| 模型 | 输入 | 输出 | 生产者 | Opset |",
        "| --- | --- | --- | --- | --- |",
    ])
    for name, model in report["models"].items():
        input_desc = ", ".join(
            f"{item['name']}:{item['shape']}:{item['element_type']}" for item in model["inputs"]
        )
        output_desc = ", ".join(
            f"{item['name']}:{item['shape']}:{item['element_type']}" for item in model["outputs"]
        )
        opsets = ", ".join(
            f"{item['domain'] or 'ai.onnx'}={item['version']}" for item in model["opsets"]
        )
        lines.append(
            f"| {name} | {input_desc} | {output_desc} | "
            f"{model['producer_name']} {model['producer_version']} | {opsets} |"
        )
    camera = report["camera_preprocess"]
    lines.extend([
        "",
        "## 深度视觉契约",
        "",
        f"裁剪后单帧为 {camera['crop_width']}×{camera['crop_height']}，"
        f"历史抽头数为 {len(camera['history_taps'])}，编码器输入共 "
        f"{camera['encoder_input_elements']} 个 float32，输出 "
        f"{camera['encoder_output_elements']} 维。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    try:
        report = audit(args.project_root.resolve())
    except Exception as error:
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
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
