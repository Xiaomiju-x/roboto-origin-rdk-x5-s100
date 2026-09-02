#!/usr/bin/env bash

set -e

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
deps_root="$project_root/deps"

source "$project_root/scripts/env_x5.sh"

prepend_path() {
  local variable_name="$1"
  local candidate="$2"
  local current_value="${!variable_name:-}"

  [ -d "$candidate" ] || return 0
  case ":$current_value:" in
    *":$candidate:"*) ;;
    *) export "$variable_name=$candidate${current_value:+:$current_value}" ;;
  esac
}

prepend_path CMAKE_PREFIX_PATH "$deps_root/install"
prepend_path CMAKE_PREFIX_PATH "$deps_root/install/librealsense"
prepend_path CMAKE_PREFIX_PATH "$deps_root/install/livox_sdk2"
prepend_path LD_LIBRARY_PATH "$deps_root/install/librealsense/lib"
prepend_path LD_LIBRARY_PATH "$deps_root/install/livox_sdk2/lib"

for overlay in ros_pcl livox_ros_driver2 realsense_ros; do
  setup_file="$deps_root/install/$overlay/setup.bash"
  if [ -f "$setup_file" ]; then
    source "$setup_file"
  fi
done

navigation_setup="$deps_root/install/official_navigation/setup.bash"
navigation_marker="$deps_root/install/official_navigation/robots_localization/share/robots_localization/local_setup.bash"
if [ -f "$navigation_setup" ] && [ -f "$navigation_marker" ]; then
  source "$navigation_setup"
fi

deploy_setup="$deps_root/install/official_deploy/setup.bash"
deploy_marker="$deps_root/install/official_deploy/roboparty_inference/share/roboparty_inference/local_setup.bash"
if [ -f "$deploy_setup" ] && [ -f "$deploy_marker" ]; then
  source "$deploy_setup"
fi

export Sophus_DIR="$deps_root/install/share/sophus/cmake"
export realsense2_DIR="$deps_root/install/librealsense/lib/cmake/realsense2"
export ROBOTO_OPEN3D_PYTHON="$deps_root/venv/open3d/bin/python"

if [ "$ROS_DOMAIN_ID" != "42" ]; then
  printf 'ERROR: dependency overlay changed ROS_DOMAIN_ID to %s\n' "$ROS_DOMAIN_ID" >&2
  return 1 2>/dev/null || exit 1
fi

printf 'Roboto X5 dependency overlay active: %s\n' "$deps_root"
