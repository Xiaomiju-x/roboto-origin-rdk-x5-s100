#!/usr/bin/env python3
"""Audit official ROS package manifests against the captured X5 inventory.

This is a static/read-only audit. It parses package.xml files and captured
package lists; it does not invoke rosdep, install software, build a workspace,
start ROS nodes, or access hardware devices.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any


DEPENDENCY_TAGS = {
    "depend",
    "build_depend",
    "build_export_depend",
    "buildtool_depend",
    "exec_depend",
}

SYSTEM_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "boost": ("libboost-all-dev",),
    "eigen": ("libeigen3-dev",),
    "fmt": ("libfmt-dev",),
    "libopencv-dev": ("libopencv-dev",),
    "librealsense2": ("librealsense2-dev", "ros-humble-librealsense2"),
    "python3": ("python3",),
    "python3-dev": ("python3-dev",),
    "spdlog": ("libspdlog-dev",),
}


def read_lines(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def read_dpkg(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, _, version = line.partition("\t")
        packages[name.split(":", 1)[0]] = version
    return packages


def package_manifests(roots: list[Path]) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for base in roots:
        for path in sorted(base.rglob("package.xml")):
            root = ET.parse(path).getroot()
            name_node = root.find("name")
            if name_node is None or not name_node.text:
                raise ValueError(f"package name missing in {path}")
            dependencies: list[dict[str, str]] = []
            for node in root:
                if node.tag not in DEPENDENCY_TAGS or not node.text or not node.text.strip():
                    continue
                dependencies.append(
                    {
                        "name": node.text.strip(),
                        "kind": node.tag,
                        "condition": node.get("condition", ""),
                    }
                )
            manifests.append(
                {
                    "name": name_node.text.strip(),
                    "path": path,
                    "dependencies": dependencies,
                }
            )
    return manifests


def classify_dependency(
    package_name: str,
    dependency: dict[str, str],
    source_packages: set[str],
    ros_packages: set[str],
    dpkg_packages: dict[str, str],
) -> tuple[str, str]:
    name = dependency["name"]
    if name in source_packages:
        return "SOURCE", "present in pinned official source tree"
    if name in ros_packages:
        return "ROS", "present in captured `ros2 pkg list`"
    if name in SYSTEM_DEPENDENCIES:
        candidates = SYSTEM_DEPENDENCIES[name]
        installed = [candidate for candidate in candidates if candidate in dpkg_packages]
        if installed:
            return "SYSTEM", "installed: " + ", ".join(installed)
        return "MISSING", "none installed: " + ", ".join(candidates)
    if package_name == "serial" and name == "catkin":
        return "WARN", "stale manifest entry: package exports and CMakeLists use ament_cmake"
    return "MISSING", "not found in source, ROS package list, or known system mapping"


def probe_state(text: str, section: str, name: str) -> str:
    active = ""
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            active = line[1:-1]
            continue
        if active != section:
            continue
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == name:
            return fields[0]
    return "UNKNOWN"


def audit(project_root: Path) -> dict[str, Any]:
    evidence = project_root / "evidence" / "x5" / "phase_a"
    ros_packages = read_lines(evidence / "ros2_pkg_list_20260828.txt")
    dpkg_packages = read_dpkg(evidence / "dpkg_package_list_20260828.txt")
    probe_text = (evidence / "dependency_probe_20260828.txt").read_text(encoding="utf-8")
    roots = [
        project_root / "upstream" / "roboparty_deploy",
        project_root / "upstream" / "roboparty_navigation",
    ]
    manifests = package_manifests(roots)
    source_packages = {item["name"] for item in manifests}

    findings: list[dict[str, str]] = []
    packages: list[dict[str, Any]] = []
    for manifest in manifests:
        dependencies: list[dict[str, str]] = []
        for dependency in manifest["dependencies"]:
            status, detail = classify_dependency(
                manifest["name"], dependency, source_packages, ros_packages, dpkg_packages
            )
            item = {**dependency, "status": status, "detail": detail}
            dependencies.append(item)
            if status in {"MISSING", "WARN"}:
                findings.append({"package": manifest["name"], **item})
        packages.append(
            {
                "name": manifest["name"],
                "path": manifest["path"].relative_to(project_root).as_posix(),
                "dependencies": dependencies,
            }
        )

    prerequisite_checks = [
        {
            "name": "Sophus CMake package",
            "status": "FAIL" if probe_state(probe_text, "CMake package files", "SophusConfig.cmake") == "MISSING" else "PASS",
            "detail": "required by robots_localization_ros2 CMakeLists.txt",
        },
        {
            "name": "Open3D Python module",
            "status": "FAIL" if probe_state(probe_text, "Python module specifications", "open3d") == "MISSING" else "PASS",
            "detail": "listed by official navigation prerequisites and used by map tooling",
        },
        {
            "name": "ccache",
            "status": "WARN" if probe_state(probe_text, "Build tools", "ccache") == "MISSING" else "PASS",
            "detail": "officially listed convenience/build accelerator; not required for correctness",
        },
        {
            "name": "Ninja",
            "status": "WARN" if probe_state(probe_text, "Build tools", "ninja") == "MISSING" else "PASS",
            "detail": "recommended generator in localization package documentation; default generator remains possible",
        },
    ]

    counts = Counter(item["status"] for package in packages for item in package["dependencies"])
    prerequisite_counts = Counter(item["status"] for item in prerequisite_checks)
    blockers = [item for item in findings if item["status"] == "MISSING"]
    blockers.extend(item for item in prerequisite_checks if item["status"] == "FAIL")
    return {
        "scope": "static official deploy/navigation dependency closure against captured X5 inventory",
        "safety": "no install, build, ROS node, CAN, or device access performed",
        "result": "BLOCKED" if blockers else "PASS",
        "source_package_count": len(source_packages),
        "dependency_status_counts": dict(sorted(counts.items())),
        "prerequisite_status_counts": dict(sorted(prerequisite_counts.items())),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "findings": findings,
        "prerequisite_checks": prerequisite_checks,
        "packages": packages,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# X5 ROS dependency audit",
        "",
        f"Result: **{report['result']}**",
        "",
        report["safety"] + ".",
        "",
        f"- Source packages inspected: {report['source_package_count']}",
        f"- Blocking findings: {report['blocker_count']}",
        f"- Manifest dependency statuses: {report['dependency_status_counts']}",
        "",
        "## Blockers and warnings",
        "",
        "| Status | Package/check | Dependency | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for item in report["findings"]:
        lines.append(
            f"| {item['status']} | {item['package']} | {item['name']} | {item['detail']} |"
        )
    for item in report["prerequisite_checks"]:
        if item["status"] != "PASS":
            lines.append(f"| {item['status']} | prerequisite | {item['name']} | {item['detail']} |")
    lines += [
        "",
        "## Interpretation",
        "",
        "Nav2 is present, but the official localization/camera source tree is not build-ready on this X5 snapshot. "
        "The missing items are recorded for a later controlled dependency plan; this audit does not authorize installation.",
        "",
        "`serial/package.xml` still declares `catkin`, while its export and CMakeLists use `ament_cmake`; "
        "the audit treats this as a source-manifest warning rather than an X5 package blocker.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()

    report = audit(args.project_root.resolve())
    rendered_json = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    rendered_markdown = markdown(report)
    if args.json_out:
        args.json_out.write_text(rendered_json, encoding="utf-8", newline="\n")
    if args.markdown_out:
        args.markdown_out.write_text(rendered_markdown, encoding="utf-8", newline="\n")
    print(f"RESULT {report['result']} blockers={report['blocker_count']}")
    return 1 if report["result"] == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
