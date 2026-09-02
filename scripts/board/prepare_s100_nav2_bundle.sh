#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
DEBS="$ROOT/third_party/debs/nav2"
PREFIX="$ROOT/install/nav2_bundle"
RECEIPT="$ROOT/evidence/d2_nav2_package_receipt.json"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

packages=(
  libgraphicsmagick++-q16-12
  libgraphicsmagick-q16-3
  ros-humble-angles
  ros-humble-bond
  ros-humble-bondcpp
  ros-humble-diagnostic-updater
  ros-humble-laser-geometry
  ros-humble-map-msgs
  ros-humble-nav2-common
  ros-humble-nav2-core
  ros-humble-nav2-costmap-2d
  ros-humble-nav2-lifecycle-manager
  ros-humble-nav2-map-server
  ros-humble-nav2-msgs
  ros-humble-nav2-navfn-planner
  ros-humble-nav2-planner
  ros-humble-nav2-util
  ros-humble-nav2-voxel-grid
  ros-humble-smclib
)

mkdir -p "$DEBS" "$PREFIX"
cd "$DEBS"
for package in "${packages[@]}"; do
  version="$(apt-cache policy "$package" | awk '/Candidate:/{print $2}')"
  if [ -z "$version" ] || [ "$version" = '(none)' ]; then
    printf 'ERROR: no candidate for %s\n' "$package" >&2
    exit 1
  fi
  pattern="${package}_${version}_"'*.deb'
  if ! compgen -G "$pattern" >/dev/null; then
    apt-get download "$package=$version"
  fi
done

for archive in ./*.deb; do
  dpkg-deb -x "$archive" "$PREFIX"
done

python3 - "$DEBS" "$PREFIX" "$RECEIPT" <<'PY'
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

debs, prefix, output = map(pathlib.Path, sys.argv[1:])
archives = []
for path in sorted(debs.glob("*.deb")):
    package = subprocess.check_output(["dpkg-deb", "-f", str(path), "Package"], text=True).strip()
    version = subprocess.check_output(["dpkg-deb", "-f", str(path), "Version"], text=True).strip()
    archives.append({
        "package": package,
        "version": version,
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
required = ["nav2_planner", "nav2_map_server", "nav2_lifecycle_manager", "nav2_navfn_planner"]
checks = {
    name: (prefix / "opt/ros/humble/share" / name / "package.xml").is_file()
    for name in required
}
payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "scope": "download and extract ROS packages inside project only; dpkg database unchanged",
    "archives": archives,
    "required_packages": checks,
    "system_install_performed": False,
    "result": "PASS" if archives and all(checks.values()) else "FAIL",
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

source "$ROOT/scripts/env_s100_nav2.sh" >/dev/null
for package in nav2_planner nav2_map_server nav2_lifecycle_manager nav2_navfn_planner; do
  ros2 pkg prefix "$package"
done
bash "$ROOT/scripts/verify_s100_safety.sh"
