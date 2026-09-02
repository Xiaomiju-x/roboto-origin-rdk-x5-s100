#!/usr/bin/env bash

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
NAV2_ROOT="$ROOT/install/nav2_bundle/opt/ros/humble"
NAV2_BUNDLE_ROOT="$ROOT/install/nav2_bundle"
ADAPTER_ROOT="$ROOT/install/nav2_adapter"
source "$ROOT/scripts/env_s100.sh"

for prefix in "$NAV2_ROOT" "$ADAPTER_ROOT"; do
  if [ -d "$prefix" ]; then
    export AMENT_PREFIX_PATH="$prefix${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
    export CMAKE_PREFIX_PATH="$prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
    export PATH="$prefix/bin${PATH:+:$PATH}"
    export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/aarch64-linux-gnu:$prefix/usr/lib:$prefix/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export PYTHONPATH="$prefix/local/lib/python3.10/dist-packages:$prefix/lib/python3.10/site-packages${PYTHONPATH:+:$PYTHONPATH}"
  fi
done

export LD_LIBRARY_PATH="$NAV2_BUNDLE_ROOT/usr/lib:$NAV2_BUNDLE_ROOT/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
