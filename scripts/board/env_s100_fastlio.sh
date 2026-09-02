#!/usr/bin/env bash

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
DEB_ROOT="$ROOT/install/fastlio_bundle"
ROS_PCL_ROOT="$DEB_ROOT/opt/ros/humble"
SOPHUS_ROOT="$ROOT/install/sophus-1.22.10"
LIVOX_SDK_ROOT="$ROOT/install/livox_sdk2-v1.3.1"
NLINK_ROOT="$ROOT/install/nlink_message"
LIVOX_DRIVER_ROOT="$ROOT/install/livox_ros_driver2-v1.2.6"
LOCALIZATION_ROOT="$ROOT/install/robots_localization"

source "$ROOT/scripts/env_s100.sh"

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

prepend_path PATH "$ROOT/third_party/bin"

for prefix in \
  "$DEB_ROOT/usr" \
  "$ROS_PCL_ROOT" \
  "$SOPHUS_ROOT" \
  "$LIVOX_SDK_ROOT" \
  "$NLINK_ROOT" \
  "$LIVOX_DRIVER_ROOT" \
  "$LOCALIZATION_ROOT"; do
  prepend_path CMAKE_PREFIX_PATH "$prefix"
  prepend_path AMENT_PREFIX_PATH "$prefix"
  prepend_path PATH "$prefix/bin"
  prepend_path LD_LIBRARY_PATH "$prefix/lib"
  prepend_path LD_LIBRARY_PATH "$prefix/lib/aarch64-linux-gnu"
  prepend_path PYTHONPATH "$prefix/lib/python3.10/site-packages"
  prepend_path PYTHONPATH "$prefix/local/lib/python3.10/dist-packages"
done

prepend_path LD_LIBRARY_PATH "$DEB_ROOT/usr/lib"
prepend_path LD_LIBRARY_PATH "$DEB_ROOT/usr/lib/aarch64-linux-gnu"

export Sophus_DIR="$SOPHUS_ROOT/share/sophus/cmake"
export Ceres_DIR="$DEB_ROOT/usr/lib/cmake/Ceres"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

if [ "$ROS_DOMAIN_ID" != "42" ]; then
  printf 'ERROR: FAST-LIO overlay changed ROS_DOMAIN_ID to %s\n' "$ROS_DOMAIN_ID" >&2
  return 1 2>/dev/null || exit 1
fi
