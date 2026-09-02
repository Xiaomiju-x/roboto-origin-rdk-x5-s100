#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
deps_root="$project_root/deps"
evidence_dir="$project_root/evidence/x5/dependencies"
stamp="$(date +%Y%m%dT%H%M%S%z)"
output_file="$evidence_dir/dependency_overlay_$stamp.txt"
failures=0

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
mkdir -p "$evidence_dir"

present_file() {
  local label="$1"
  local path="$2"
  if [ -e "$path" ]; then
    printf 'PRESENT\t%s\t%s\n' "$label" "$path"
    sha256sum "$path" 2>/dev/null || true
  else
    printf 'MISSING\t%s\t%s\n' "$label" "$path"
    failures=$((failures + 1))
  fi
}

{
  printf 'captured_at=%s\n' "$(date --iso-8601=seconds)"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'machine=%s\n' "$(uname -m)"
  printf 'kernel=%s\n' "$(uname -r)"
  printf 'glibc=%s\n' "$(getconf GNU_LIBC_VERSION)"
  printf 'python=%s\n' "$(python3 -V 2>&1)"
  printf 'ROS_DOMAIN_ID=%s\n' "$ROS_DOMAIN_ID"

  printf '\n[Installed artifacts]\n'
  present_file SophusConfig "$deps_root/install/share/sophus/cmake/SophusConfig.cmake"
  present_file ros_pcl_setup "$deps_root/install/ros_pcl/setup.bash"
  present_file livox_sdk_shared "$deps_root/install/livox_sdk2/lib/liblivox_lidar_sdk_shared.so"
  present_file livox_driver_setup "$deps_root/install/livox_ros_driver2/setup.bash"
  present_file librealsense "$deps_root/install/librealsense/lib/librealsense2.so.2.58.1"
  present_file realsense_ros_setup "$deps_root/install/realsense_ros/setup.bash"
  present_file open3d_python "$deps_root/venv/open3d/bin/python"
  present_file navigation_python_bundle "$deps_root/cache/navigation-offline-x5-pydeps-cp310-aarch64.tar.gz"
  present_file navigation_node "$deps_root/install/official_navigation/robots_localization/lib/robots_localization/robots_localization_node"
  present_file camera_depth_node "$deps_root/install/official_deploy/camera/lib/camera/depth_node"
  present_file inference_node "$deps_root/install/official_deploy/roboparty_inference/lib/roboparty_inference/inference_node"
  present_file navigation_patch "$project_root/patches/roboparty-navigation-x5-build.patch"
  present_file deploy_patch "$project_root/patches/roboparty-deploy-x5-build.patch"

  printf '\n[ROS package prefixes]\n'
  for package in \
    pcl_msgs pcl_conversions pcl_ros perception_pcl \
    livox_ros_driver2 realsense2_camera_msgs realsense2_camera realsense2_description \
    nlink_message serial nlink_parser_ros2 nav2_localization_adapter robots_localization \
    camera roboparty_imu roboparty_motors roboparty_inference; do
    if prefix="$(ros2 pkg prefix "$package" 2>/dev/null)"; then
      printf 'PRESENT\t%s\t%s\n' "$package" "$prefix"
    else
      printf 'MISSING\t%s\n' "$package"
      failures=$((failures + 1))
    fi
  done

  printf '\n[Open3D synthetic probe]\n'
  if env -u PYTHONPATH "$deps_root/venv/open3d/bin/python" -c \
    'import open3d as o3d; p=o3d.geometry.PointCloud(); assert len(p.points) == 0; print("version=" + o3d.__version__)'; then
    printf 'PASS\tOpen3D import and empty point cloud\n'
  else
    printf 'FAIL\tOpen3D import or empty point cloud\n'
    failures=$((failures + 1))
  fi
  if env -u PYTHONPATH "$deps_root/venv/open3d/bin/python" -m pip check; then
    printf 'PASS\tOpen3D virtual environment dependency check\n'
  else
    printf 'FAIL\tOpen3D virtual environment dependency check\n'
    failures=$((failures + 1))
  fi

  printf '\n[Navigation offline Python]\n'
  if env -u PYTHONPATH "$deps_root/venv/open3d/bin/python" -c \
    'import PIL, scipy; print("Pillow=" + PIL.__version__ + " SciPy=" + scipy.__version__)'; then
    printf 'PASS\tPillow and SciPy import from project virtual environment\n'
  else
    printf 'FAIL\tPillow or SciPy import from project virtual environment\n'
    failures=$((failures + 1))
  fi

  printf '\n[Dynamic linking]\n'
  for library in \
    "$deps_root/install/livox_sdk2/lib/liblivox_lidar_sdk_shared.so" \
    "$deps_root/install/librealsense/lib/librealsense2.so.2.58.1" \
    "$deps_root/install/official_navigation/robots_localization/lib/robots_localization/robots_localization_node" \
    "$deps_root/install/official_navigation/robots_localization/lib/libscan_aligner.so" \
    "$deps_root/install/official_navigation/robots_localization/lib/libimu_processor.so" \
    "$deps_root/install/official_deploy/camera/lib/camera/depth_node" \
    "$deps_root/install/official_deploy/roboparty_inference/lib/roboparty_inference/inference_node" \
    "$deps_root/install/official_deploy/roboparty_imu/lib/python3.10/site-packages/imu_py.cpython-310-aarch64-linux-gnu.so" \
    "$deps_root/install/official_deploy/roboparty_motors/lib/python3.10/site-packages/motors_py.cpython-310-aarch64-linux-gnu.so" \
    "$deps_root/install/official_deploy/roboparty_inference/lib/python3.10/site-packages/robot_py.cpython-310-aarch64-linux-gnu.so"; do
    printf 'library=%s\n' "$library"
    if [ -f "$library" ]; then
      ldd "$library" 2>&1
      if ldd "$library" 2>&1 | grep -q 'not found'; then
        printf 'FAIL\tunresolved dynamic dependency\n'
        failures=$((failures + 1))
      else
        printf 'PASS\tdynamic dependencies resolved\n'
      fi
    fi
  done

  printf '\n[Official checkout integrity]\n'
  for repository in \
    "$project_root/src/official/roboparty_deploy" \
    "$project_root/src/official/roboparty_navigation"; do
    dirty="$(git -C "$repository" status --porcelain --ignore-submodules=none)"
    if [ -z "$dirty" ]; then
      printf 'PASS\tclean official checkout\t%s\n' "$repository"
    else
      printf 'FAIL\tdirty official checkout\t%s\n%s\n' "$repository" "$dirty"
      failures=$((failures + 1))
    fi
  done

  printf '\n[Safety state]\n'
  ip -brief link show can0 2>&1 || true
  if ip link show can0 2>/dev/null | grep -q 'state DOWN'; then
    printf 'PASS\tcan0 remains DOWN\n'
  else
    printf 'FAIL\tcan0 absent or not DOWN\n'
    failures=$((failures + 1))
  fi

  for service in embodied_brain.service cockpit_bridge.service eb_sensor_watchdog.service xrd-v6-8890.service; do
    state="$(systemctl is-active "$service" 2>/dev/null || true)"
    printf '%s\t%s\n' "$service" "$state"
    if [ "$state" != "inactive" ]; then
      failures=$((failures + 1))
    fi
  done

  if pgrep -f '(^|/)(depth_node|inference_node|robots_localization_node|realsense2_camera_node)( |$)' >/dev/null; then
    printf 'FAIL\tofficial runtime node process detected\n'
    pgrep -af '(^|/)(depth_node|inference_node|robots_localization_node|realsense2_camera_node)( |$)' || true
    failures=$((failures + 1))
  else
    printf 'PASS\tno official runtime node process detected\n'
  fi

  printf '\n[Result]\n'
  if [ "$failures" -eq 0 ]; then
    printf 'RESULT PASS (dependency and static-build availability only; no device or algorithm execution implied)\n'
  else
    printf 'RESULT FAIL count=%s\n' "$failures"
  fi
} > "$output_file"

sha256sum "$output_file"
printf 'evidence_file=%s\n' "$output_file"
test "$failures" -eq 0
