#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
deps_root="$project_root/deps"
phase="${1:-all}"
parallel_jobs="${ROBOTO_BUILD_JOBS:-2}"

source "$project_root/scripts/env_x5.sh" >/dev/null
bash "$project_root/scripts/verify_phase_a_env.sh"

case "$parallel_jobs" in
  ''|*[!0-9]*|0)
    printf 'ERROR: ROBOTO_BUILD_JOBS must be a positive integer\n' >&2
    exit 2
    ;;
esac

export MAKEFLAGS="-j$parallel_jobs -l$parallel_jobs"
export CMAKE_BUILD_PARALLEL_LEVEL="$parallel_jobs"

mkdir -p \
  "$deps_root/build" \
  "$deps_root/install" \
  "$deps_root/logs"

run_phase() {
  local requested="$1"
  [ "$phase" = "all" ] || [ "$phase" = "$requested" ]
}

build_sophus() {
  cmake \
    -S "$deps_root/src/Sophus" \
    -B "$deps_root/build/Sophus" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$deps_root/install" \
    -DBUILD_SOPHUS_TESTS=OFF \
    -DBUILD_SOPHUS_EXAMPLES=OFF
  cmake --build "$deps_root/build/Sophus" --parallel "$parallel_jobs"
  cmake --install "$deps_root/build/Sophus"
}

build_ros_pcl() {
  colcon --log-base "$deps_root/logs/ros_pcl" build \
    --base-paths "$deps_root/src/pcl_msgs" "$deps_root/src/perception_pcl" \
    --build-base "$deps_root/build/ros_pcl" \
    --install-base "$deps_root/install/ros_pcl" \
    --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
}

build_livox_sdk2() {
  cmake \
    -S "$deps_root/src/Livox-SDK2" \
    -B "$deps_root/build/Livox-SDK2" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$deps_root/install/livox_sdk2"
  cmake --build "$deps_root/build/Livox-SDK2" --parallel "$parallel_jobs"
  cmake --install "$deps_root/build/Livox-SDK2"
}

build_livox_driver() {
  cp "$deps_root/src/livox_ros_driver2/package_ROS2.xml" \
    "$deps_root/src/livox_ros_driver2/package.xml"
  source "$deps_root/install/ros_pcl/setup.bash"
  prepend_sdk_path="$deps_root/install/livox_sdk2/lib"
  export LD_LIBRARY_PATH="$prepend_sdk_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

  colcon --log-base "$deps_root/logs/livox_ros_driver2" build \
    --base-paths "$deps_root/src/livox_ros_driver2" \
    --packages-select livox_ros_driver2 \
    --build-base "$deps_root/build/livox_ros_driver2" \
    --install-base "$deps_root/install/livox_ros_driver2" \
    --cmake-args \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -DROS_EDITION=ROS2 \
      -DDISTRO_ROS=humble \
      -DLIVOX_LIDAR_SDK_LIBRARY="$deps_root/install/livox_sdk2/lib/liblivox_lidar_sdk_shared.so" \
      -DLIVOX_LIDAR_SDK_INCLUDE_DIR="$deps_root/install/livox_sdk2/include"
}

apply_librealsense_patch() {
  local source_dir="$deps_root/src/librealsense-2.58.1"
  local patch_file="$deps_root/patches/librealsense-use-local-json.patch"

  if git -C "$source_dir" apply --check "$patch_file" 2>/dev/null; then
    git -C "$source_dir" apply "$patch_file"
  elif git -C "$source_dir" apply --reverse --check "$patch_file" 2>/dev/null; then
    printf 'librealsense offline JSON patch already applied\n'
  else
    printf 'ERROR: librealsense source does not match the audited patch\n' >&2
    return 1
  fi
}

build_librealsense() {
  apply_librealsense_patch
  cmake \
    -S "$deps_root/src/librealsense-2.58.1" \
    -B "$deps_root/build/librealsense-2.58.1" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$deps_root/install/librealsense" \
    -DBUILD_SHARED_LIBS=ON \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_GRAPHICAL_EXAMPLES=OFF \
    -DBUILD_TOOLS=OFF \
    -DBUILD_UNIT_TESTS=OFF \
    -DBUILD_PYTHON_BINDINGS=OFF \
    -DBUILD_ROSBAG2=OFF \
    -DBUILD_WITH_CUDA=OFF \
    -DBUILD_GLSL_EXTENSIONS=OFF \
    -DCHECK_FOR_UPDATES=OFF \
    -DFORCE_RSUSB_BACKEND=ON \
    -DROBOTO_SKIP_LDCONFIG=ON \
    -DROBOTO_NLOHMANN_JSON_SOURCE="$deps_root/src/json-3.12.0"
  cmake --build "$deps_root/build/librealsense-2.58.1" --parallel "$parallel_jobs"
  cmake --install "$deps_root/build/librealsense-2.58.1"
}

build_realsense_ros() {
  source "$deps_root/install/ros_pcl/setup.bash"
  export CMAKE_PREFIX_PATH="$deps_root/install/librealsense${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
  export LD_LIBRARY_PATH="$deps_root/install/librealsense/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

  colcon --log-base "$deps_root/logs/realsense_ros" build \
    --base-paths "$project_root/src/official/roboparty_deploy/src/camera/thirdparty/realsense-ros" \
    --packages-select realsense2_camera_msgs realsense2_camera realsense2_description \
    --build-base "$deps_root/build/realsense_ros" \
    --install-base "$deps_root/install/realsense_ros" \
    --cmake-args \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -Drealsense2_DIR="$deps_root/install/librealsense/lib/cmake/realsense2"
}

install_open3d() {
  local wheel="$deps_root/cache/open3d-0.17.0-cp310-cp310-manylinux_2_27_aarch64.whl"
  local pydeps_bundle="$deps_root/cache/open3d-0.17.0-pydeps-cp310-aarch64.tar.gz"
  local expected_sha256="e28e515f4850380ab4f25842bce5f3d4c63b5565f9f8d2df141059b62250ff9e"
  local expected_pydeps_sha256="140fdc45255ea02e741e1510b07e6bf25df618ca538c6918cc7b6174ac773ff7"
  local venv="$deps_root/venv/open3d"
  local site_packages="$venv/lib/python3.10/site-packages"

  printf '%s  %s\n' "$expected_sha256" "$wheel" | sha256sum --check --strict
  printf '%s  %s\n' "$expected_pydeps_sha256" "$pydeps_bundle" | sha256sum --check --strict
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --no-index --no-deps "$wheel"
  tar -xzf "$pydeps_bundle" -C "$site_packages"
  env -u PYTHONPATH "$venv/bin/python" -m pip check
  env -u PYTHONPATH "$venv/bin/python" -c \
    'import open3d as o3d; p=o3d.geometry.PointCloud(); assert len(p.points) == 0; print(o3d.__version__)'
}

install_navigation_python() {
  local bundle="$deps_root/cache/navigation-offline-x5-pydeps-cp310-aarch64.tar.gz"
  local expected_sha256="fc3643b39af7e6f02844f254bed944b374d4aaf71c0d847447c02ac75f309fbb"
  local venv="$deps_root/venv/open3d"
  local site_packages="$venv/lib/python3.10/site-packages"

  printf '%s  %s\n' "$expected_sha256" "$bundle" | sha256sum --check --strict
  test -x "$venv/bin/python"
  test -d "$site_packages"
  tar -xzf "$bundle" -C "$site_packages"
  env -u PYTHONPATH "$venv/bin/python" -m pip check
  env -u PYTHONPATH "$venv/bin/python" -c \
    'import PIL, scipy; print("Pillow=" + PIL.__version__ + " SciPy=" + scipy.__version__)'
}

case "$phase" in
  all|sophus|ros_pcl|livox_sdk2|livox_driver|librealsense|realsense_ros|open3d|navigation_python) ;;
  *)
    printf 'Usage: %s [all|sophus|ros_pcl|livox_sdk2|livox_driver|librealsense|realsense_ros|open3d|navigation_python]\n' "$0" >&2
    exit 2
    ;;
esac

run_phase sophus && build_sophus
run_phase ros_pcl && build_ros_pcl
run_phase livox_sdk2 && build_livox_sdk2
run_phase livox_driver && build_livox_driver
run_phase librealsense && build_librealsense
run_phase realsense_ros && build_realsense_ros
run_phase open3d && install_open3d
run_phase navigation_python && install_navigation_python

printf 'Dependency build phase complete: %s\n' "$phase"
