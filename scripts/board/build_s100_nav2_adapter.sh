#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
SOURCE="$ROOT/src/official/roboparty_navigation/nav2_localization_adapter"
BUILD="$ROOT/build/nav2_adapter"
INSTALL="$ROOT/install/nav2_adapter"
source "$ROOT/scripts/env_s100_nav2.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

mkdir -p "$BUILD" "$INSTALL" "$ROOT/logs/build"
cmake -S "$SOURCE" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL" \
  -DBUILD_TESTING=OFF \
  | tee "$ROOT/logs/build/nav2_adapter_configure.log"
cmake --build "$BUILD" --parallel 2 | tee "$ROOT/logs/build/nav2_adapter_build.log"
cmake --install "$BUILD" | tee "$ROOT/logs/build/nav2_adapter_install.log"
test -x "$INSTALL/lib/nav2_localization_adapter/nav2_localization_adapter_node"
bash "$ROOT/scripts/verify_s100_safety.sh"
