#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
DEBS="$ROOT/third_party/debs/fastlio"
PREFIX="$ROOT/install/fastlio_bundle"
RECEIPT="$ROOT/evidence/d2_fastlio_package_receipt.json"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

packages=(
  libamd2
  libbtf1
  libcamd2
  libccolamd2
  libceres-dev
  libceres2
  libcholmod3
  libcxsparse3
  libklu1
  libldl2
  libmetis5
  libmongoose2
  librbio2
  libsliplu1
  libspqr2
  libsuitesparse-dev
  libsuitesparseconfig5
  libumfpack5
  ros-humble-pcl-conversions
  ros-humble-pcl-msgs
  ros-humble-pcl-ros
)

mkdir -p "$DEBS" "$PREFIX"
before_hash="$(dpkg-query -W -f='${Package}\t${Version}\t${Status}\n' | sha256sum | awk '{print $1}')"
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

# ROS binary packages encode their own /opt/ros/humble include/library paths in
# exported CMake targets. Relocate only the three extracted PCL packages; keep
# references to system dependencies such as message_filters on system ROS.
while IFS= read -r -d '' cmake_file; do
  for package_path in \
    include/pcl_conversions \
    include/pcl_msgs \
    include/pcl_ros \
    lib/libpcl_conversions \
    lib/libpcl_msgs \
    lib/libpcl_ros; do
    sed -i \
      "s#/opt/ros/humble/$package_path#$PREFIX/opt/ros/humble/$package_path#g" \
      "$cmake_file"
  done
done < <(grep -RIlZ '/opt/ros/humble' \
  "$PREFIX/opt/ros/humble/share/pcl_conversions" \
  "$PREFIX/opt/ros/humble/share/pcl_msgs" \
  "$PREFIX/opt/ros/humble/share/pcl_ros" \
  --include='*.cmake')
after_hash="$(dpkg-query -W -f='${Package}\t${Version}\t${Status}\n' | sha256sum | awk '{print $1}')"
test "$before_hash" = "$after_hash"

python3 - "$DEBS" "$PREFIX" "$RECEIPT" "$before_hash" "$after_hash" <<'PY'
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

debs, prefix, output = map(pathlib.Path, sys.argv[1:4])
before_hash, after_hash = sys.argv[4:6]
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
pcl_cmake_files = [
    path
    for package in ("pcl_conversions", "pcl_msgs", "pcl_ros")
    for path in (prefix / "opt/ros/humble/share" / package).rglob("*.cmake")
]
relocated_ros_prefix = str(prefix / "opt/ros/humble")
checks = {
    "ceres_config": (prefix / "usr/lib/cmake/Ceres/CeresConfig.cmake").is_file(),
    "pcl_conversions": (prefix / "opt/ros/humble/share/pcl_conversions/package.xml").is_file(),
    "pcl_msgs": (prefix / "opt/ros/humble/share/pcl_msgs/package.xml").is_file(),
    "pcl_ros": (prefix / "opt/ros/humble/share/pcl_ros/package.xml").is_file(),
    "pcl_cmake_relocated": any(
        relocated_ros_prefix in path.read_text(encoding="utf-8", errors="ignore")
        for path in pcl_cmake_files
    ),
    "dpkg_database_unchanged": before_hash == after_hash,
}
payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "scope": "download and extract FAST-LIO build dependencies inside project only",
    "archives": archives,
    "checks": checks,
    "dpkg_state_sha256_before": before_hash,
    "dpkg_state_sha256_after": after_hash,
    "system_install_performed": False,
    "result": "PASS" if archives and all(checks.values()) else "FAIL",
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

source "$ROOT/scripts/env_s100_fastlio.sh" >/dev/null
test -f "$Ceres_DIR/CeresConfig.cmake"
for package in pcl_conversions pcl_msgs pcl_ros; do
  ros2 pkg prefix "$package"
done
bash "$ROOT/scripts/verify_s100_safety.sh"
