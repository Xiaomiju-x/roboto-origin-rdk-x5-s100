#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
evidence_dir="$project_root/evidence/x5/phase_a"

source "$project_root/scripts/env_x5.sh" >/dev/null
mkdir -p "$evidence_dir"

dpkg_file="$evidence_dir/dpkg_package_list_20260828.txt"
probe_file="$evidence_dir/dependency_probe_20260828.txt"

dpkg-query -W -f='${binary:Package}\t${Version}\n' | LC_ALL=C sort > "$dpkg_file"
ros2 pkg list | LC_ALL=C sort > "$evidence_dir/ros2_pkg_list_20260828.txt"

{
  printf 'captured_at=%s\n' "$(date --iso-8601=seconds)"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'ROS_DOMAIN_ID=%s\n' "$ROS_DOMAIN_ID"
  printf '\n[ROS package prefixes]\n'
  for package in \
    livox_ros_driver2 pcl_conversions pcl_ros realsense2_camera \
    realsense2_camera_msgs nav2_bringup robot_localization; do
    if prefix="$(ros2 pkg prefix "$package" 2>/dev/null)"; then
      printf 'PRESENT\t%s\t%s\n' "$package" "$prefix"
    else
      printf 'MISSING\t%s\n' "$package"
    fi
  done

  printf '\n[System packages]\n'
  for package in \
    libboost-all-dev libeigen3-dev libfmt-dev libopencv-dev \
    libceres-dev librealsense2-dev librealsense2-udev libspdlog-dev \
    libpcl-dev pybind11-dev python3 python3-dev qtbase5-dev zlib1g-dev \
    ros-humble-catkin ros-humble-pcl-conversions ros-humble-pcl-ros; do
    if version="$(dpkg-query -W -f='${Version}' "$package" 2>/dev/null)"; then
      printf 'PRESENT\t%s\t%s\n' "$package" "$version"
    else
      printf 'MISSING\t%s\n' "$package"
    fi
  done

  printf '\n[Build tools]\n'
  for command_name in cmake colcon ccache ninja; do
    if command_path="$(command -v "$command_name" 2>/dev/null)"; then
      printf 'PRESENT\t%s\t%s\n' "$command_name" "$command_path"
    else
      printf 'MISSING\t%s\n' "$command_name"
    fi
  done

  printf '\n[APT cached candidates]\n'
  for package in \
    ccache ninja-build libsophus-dev librealsense2-dev python3-open3d \
    ros-humble-librealsense2 ros-humble-pcl-conversions ros-humble-pcl-ros; do
    candidate="$(apt-cache policy "$package" 2>/dev/null | sed -n 's/^[[:space:]]*Candidate:[[:space:]]*//p' | head -n 1 || true)"
    if [ -n "$candidate" ] && [ "$candidate" != "(none)" ]; then
      printf 'PRESENT\t%s\t%s\n' "$package" "$candidate"
    else
      printf 'MISSING\t%s\n' "$package"
    fi
  done

  printf '\n[CMake package files]\n'
  sophus_files="$(find /usr/local /usr/lib /usr/share -iname 'SophusConfig.cmake' -print 2>/dev/null || true)"
  if [ -n "$sophus_files" ]; then
    while IFS= read -r path; do
      printf 'PRESENT\tSophus\t%s\n' "$path"
    done <<< "$sophus_files"
  else
    printf 'MISSING\tSophusConfig.cmake\n'
  fi

  printf '\n[Python module specifications]\n'
  python3 - <<'PY'
import importlib.util

for name in ("open3d", "numpy", "yaml", "PIL", "scipy"):
    spec = importlib.util.find_spec(name)
    if spec is None:
        print(f"MISSING\t{name}")
    else:
        print(f"PRESENT\t{name}\t{spec.origin or 'namespace'}")
PY

  printf '\n[CAN state]\n'
  ip -brief link show can0 2>&1 || true
} > "$probe_file"

sha256sum "$dpkg_file" "$evidence_dir/ros2_pkg_list_20260828.txt" "$probe_file"
