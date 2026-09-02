#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
KISS="$ROOT/src/third_party/kiss-icp-v1.3.0"
ROBIN="$ROOT/src/third_party/robin-map-v1.4.0"
SOPHUS_ARCHIVE="$ROOT/third_party/archives/Sophus-1.22.10.tar.gz"
SOPHUS_SOURCE="$ROOT/src/third_party/Sophus"
SOPHUS_BUILD="$ROOT/build/sophus-1.22.10"
SOPHUS_INSTALL="$ROOT/install/sophus-1.22.10"
BUILD="$ROOT/build/kiss_icp-v1.3.0"
INSTALL="$ROOT/install/kiss_icp-v1.3.0"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

test "$(sha256sum "$SOPHUS_ARCHIVE" | awk '{print $1}')" = \
  cb5f05b3238aa86701f71cf6a74a25abe64206c3fd9d5f974a93bdd458d6a216
test -f "$KISS/ros/CMakeLists.txt"
test -f "$ROBIN/LICENSE"

if [ ! -f "$SOPHUS_SOURCE/CMakeLists.txt" ]; then
  tar -xzf "$SOPHUS_ARCHIVE" -C "$ROOT/src/third_party"
fi
mkdir -p "$SOPHUS_BUILD" "$SOPHUS_INSTALL" "$BUILD" "$INSTALL" "$ROOT/logs/build"
export CMAKE_BUILD_PARALLEL_LEVEL=2

cmake -S "$SOPHUS_SOURCE" -B "$SOPHUS_BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$SOPHUS_INSTALL" \
  -DBUILD_SOPHUS_TESTS=OFF -DBUILD_SOPHUS_EXAMPLES=OFF \
  | tee "$ROOT/logs/build/sophus_configure.log"
cmake --build "$SOPHUS_BUILD" --parallel 2 | tee "$ROOT/logs/build/sophus_build.log"
cmake --install "$SOPHUS_BUILD" | tee "$ROOT/logs/build/sophus_install.log"

export CMAKE_PREFIX_PATH="$SOPHUS_INSTALL${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
cmake -S "$KISS/ros" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL" \
  -DBUILD_TESTING=OFF \
  -DUSE_CCACHE=OFF \
  -DUSE_SYSTEM_EIGEN3=ON \
  -DUSE_SYSTEM_SOPHUS=ON \
  -DSophus_DIR="$SOPHUS_INSTALL/share/sophus/cmake" \
  -DUSE_SYSTEM_TBB=ON \
  -DUSE_SYSTEM_TSL-ROBIN-MAP=OFF \
  -DFETCHCONTENT_SOURCE_DIR_TESSIL="$ROBIN" \
  | tee "$ROOT/logs/build/kiss_icp_configure.log"
cmake --build "$BUILD" --parallel 2 | tee "$ROOT/logs/build/kiss_icp_build.log"
cmake --install "$BUILD" | tee "$ROOT/logs/build/kiss_icp_install.log"

test -x "$INSTALL/lib/kiss_icp/kiss_icp_node"
python3 - "$INSTALL" "$ROOT/evidence/d2_kiss_icp_build_receipt.json" <<'PY'
import datetime
import hashlib
import json
import pathlib
import platform
import sys

install, output = map(pathlib.Path, sys.argv[1:])
binary = install / "lib/kiss_icp/kiss_icp_node"
library = install / "lib/libodometry_component.so"
payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "result": "PASS",
    "scope": "project-only S100 build of KISS-ICP v1.3.0",
    "machine": platform.machine(),
    "source_commit": "b16835283aee62f7d5e2bdf6c1c3bb2930de74ff",
    "sophus_commit": "de0f8d3d92bf776271e16de56d1803940ebccab9",
    "robin_map_commit": "4ec1bf19c6a96125ea22062f38c2cf5b958e448e",
    "artifacts": [
        {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (binary, library)
    ],
    "system_install_performed": False,
    "device_access": False,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
