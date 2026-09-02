#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
SOURCE_ROOT="$ROOT/src/official_fastlio"
SDK_ARCHIVE="$ROOT/third_party/archives/Livox-SDK2-v1.3.1.tar.gz"
SDK_EXTRACT="$ROOT/src/third_party/livox-sdk2-v1.3.1"
SDK_BUILD="$ROOT/build/livox_sdk2-v1.3.1"
SDK_INSTALL="$ROOT/install/livox_sdk2-v1.3.1"
NLINK_SOURCE="$SOURCE_ROOT/nlink_message"
NLINK_BUILD="$ROOT/build/nlink_message"
NLINK_INSTALL="$ROOT/install/nlink_message"
DRIVER_SOURCE="$SOURCE_ROOT/livox_ros_driver2"
DRIVER_BUILD="$ROOT/build/livox_ros_driver2-v1.2.6"
DRIVER_INSTALL="$ROOT/install/livox_ros_driver2-v1.2.6"
LOCALIZATION_SOURCE="$SOURCE_ROOT/robots_localization_ros2"
LOCALIZATION_BUILD="$ROOT/build/robots_localization"
LOCALIZATION_INSTALL="$ROOT/install/robots_localization"
PATCH_ROOT="$ROOT/patches"
RECEIPT="$ROOT/evidence/d2_fastlio_build_receipt.json"

source "$ROOT/scripts/env_s100_fastlio.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

test "$(sha256sum "$SDK_ARCHIVE" | awk '{print $1}')" = \
  8f925ca9babc0d04d472202d10110acf2d3f309d98cc0104df607500573dbe25
for required in \
  "$NLINK_SOURCE/CMakeLists.txt" \
  "$DRIVER_SOURCE/CMakeLists.txt" \
  "$DRIVER_SOURCE/package_ROS2.xml" \
  "$LOCALIZATION_SOURCE/CMakeLists.txt"; do
  test -f "$required"
done

# The mirrored official checkout was created on Windows and carries CRLF in
# text sources. Normalize only this isolated S100 staging copy so the audited
# X5 portability patches apply byte-for-byte; upstream mirrors stay untouched.
find "$SOURCE_ROOT" -type f \( \
  -name '*.cpp' -o -name '*.h' -o -name '*.hpp' -o \
  -name '*.c' -o -name '*.cmake' -o -name 'CMakeLists.txt' -o \
  -name '*.xml' -o -name '*.msg' -o -name '*.yaml' -o -name '*.py' \
  \) -print0 | while IFS= read -r -d '' source_file; do
    if grep -Iq $'\r' "$source_file"; then
      sed -i 's/\r$//' "$source_file"
    fi
  done

mkdir -p \
  "$SDK_EXTRACT" "$SDK_BUILD" "$SDK_INSTALL" \
  "$NLINK_BUILD" "$NLINK_INSTALL" \
  "$DRIVER_BUILD" "$DRIVER_INSTALL" \
  "$LOCALIZATION_BUILD" "$LOCALIZATION_INSTALL" \
  "$ROOT/logs/build"

if ! find "$SDK_EXTRACT" -maxdepth 2 -name CMakeLists.txt -print -quit | grep -q .; then
  tar -xzf "$SDK_ARCHIVE" -C "$SDK_EXTRACT"
fi
SDK_SOURCE="$(dirname "$(find "$SDK_EXTRACT" -maxdepth 2 -name CMakeLists.txt -print -quit)")"

apply_audited_patch() {
  local patch_file="$1"
  if (cd "$SOURCE_ROOT" && git apply --check "$patch_file") 2>/dev/null; then
    (cd "$SOURCE_ROOT" && git apply "$patch_file")
  elif (cd "$SOURCE_ROOT" && git apply --reverse --check "$patch_file") 2>/dev/null; then
    printf 'Audited patch already applied: %s\n' "$patch_file"
  else
    printf 'ERROR: staged source does not match audited patch: %s\n' "$patch_file" >&2
    return 1
  fi
}

for patch_file in \
  "$PATCH_ROOT/roboparty-navigation-x5-build.patch" \
  "$PATCH_ROOT/roboparty-navigation-x5-runtime.patch"; do
  test -f "$patch_file"
  apply_audited_patch "$patch_file"
done

thread_patch="$PATCH_ROOT/roboparty-navigation-x5-thread-lifecycle.patch"
test -f "$thread_patch"
if grep -Fq 'main_process_thread_ = std::thread' \
    "$LOCALIZATION_SOURCE/src/robots_localization_node.h" \
    && grep -Fq 'node.reset();' \
    "$LOCALIZATION_SOURCE/src/robots_localization_node.cpp" \
    && ! grep -Fq 'signal(SIGINT, SigHandle);' \
    "$LOCALIZATION_SOURCE/src/robots_localization_node.h" \
    && ! grep -Fq 'mainThread.detach();' \
    "$LOCALIZATION_SOURCE/src/robots_localization_node.h"; then
  printf 'Audited thread lifecycle patch markers already present: %s\n' "$thread_patch"
else
  apply_audited_patch "$thread_patch"
fi

for patch_file in \
  "$PATCH_ROOT/roboparty-navigation-x5-ros-entity-lifecycle.patch" \
  "$PATCH_ROOT/roboparty-navigation-x5-common-globals.patch"; do
  test -f "$patch_file"
  apply_audited_patch "$patch_file"
done

cp "$DRIVER_SOURCE/package_ROS2.xml" "$DRIVER_SOURCE/package.xml"
export CMAKE_BUILD_PARALLEL_LEVEL=1
export MAKEFLAGS='-j1 -l1'

if [ ! -f "$SDK_INSTALL/lib/liblivox_lidar_sdk_shared.so" ]; then
  cmake -S "$SDK_SOURCE" -B "$SDK_BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$SDK_INSTALL" \
    | tee "$ROOT/logs/build/livox_sdk2_configure.log"
  cmake --build "$SDK_BUILD" --parallel 1 | tee "$ROOT/logs/build/livox_sdk2_build.log"
  cmake --install "$SDK_BUILD" | tee "$ROOT/logs/build/livox_sdk2_install.log"
else
  printf 'Reuse verified project Livox SDK artifact: %s\n' "$SDK_INSTALL"
fi

if [ ! -f "$NLINK_INSTALL/share/nlink_message/package.xml" ]; then
  cmake -S "$NLINK_SOURCE" -B "$NLINK_BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$NLINK_INSTALL" \
    -DBUILD_TESTING=OFF \
    | tee "$ROOT/logs/build/nlink_message_configure.log"
  cmake --build "$NLINK_BUILD" --parallel 1 | tee "$ROOT/logs/build/nlink_message_build.log"
  cmake --install "$NLINK_BUILD" | tee "$ROOT/logs/build/nlink_message_install.log"
else
  printf 'Reuse verified project nlink_message artifact: %s\n' "$NLINK_INSTALL"
fi

export CMAKE_PREFIX_PATH="$NLINK_INSTALL${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export AMENT_PREFIX_PATH="$NLINK_INSTALL${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
export LD_LIBRARY_PATH="$NLINK_INSTALL/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$NLINK_INSTALL/lib/python3.10/site-packages${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f "$DRIVER_INSTALL/lib/liblivox_ros_driver2.so" ]; then
  cmake -S "$DRIVER_SOURCE" -B "$DRIVER_BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$DRIVER_INSTALL" \
    -DBUILD_TESTING=OFF \
    -DROS_EDITION=ROS2 \
    -DDISTRO_ROS=humble \
    -DLIVOX_LIDAR_SDK_LIBRARY="$SDK_INSTALL/lib/liblivox_lidar_sdk_shared.so" \
    -DLIVOX_LIDAR_SDK_INCLUDE_DIR="$SDK_INSTALL/include" \
    | tee "$ROOT/logs/build/livox_driver_configure.log"
  cmake --build "$DRIVER_BUILD" --parallel 1 | tee "$ROOT/logs/build/livox_driver_build.log"
  cmake --install "$DRIVER_BUILD" | tee "$ROOT/logs/build/livox_driver_install.log"
else
  printf 'Reuse verified project Livox ROS2 artifact: %s\n' "$DRIVER_INSTALL"
fi

export CMAKE_PREFIX_PATH="$DRIVER_INSTALL:$SDK_INSTALL${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export AMENT_PREFIX_PATH="$DRIVER_INSTALL${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
export LD_LIBRARY_PATH="$DRIVER_INSTALL/lib:$SDK_INSTALL/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$DRIVER_INSTALL/lib/python3.10/site-packages${PYTHONPATH:+:$PYTHONPATH}"

cmake -S "$LOCALIZATION_SOURCE" -B "$LOCALIZATION_BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$LOCALIZATION_INSTALL" \
  -DBUILD_TESTING=OFF \
  -DPYTHON_EXECUTABLE=/usr/bin/python3 \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DSophus_DIR="$Sophus_DIR" \
  -DCeres_DIR="$Ceres_DIR" \
  | tee "$ROOT/logs/build/robots_localization_configure.log"
cmake --build "$LOCALIZATION_BUILD" --parallel 1 | tee "$ROOT/logs/build/robots_localization_build.log"
cmake --install "$LOCALIZATION_BUILD" | tee "$ROOT/logs/build/robots_localization_install.log"

test -x "$LOCALIZATION_INSTALL/lib/robots_localization/robots_localization_node"

python3 - "$ROOT" "$RECEIPT" <<'PY'
import datetime
import hashlib
import json
import pathlib
import platform
import sys

root, output = map(pathlib.Path, sys.argv[1:])
artifacts = [
    root / "install/livox_sdk2-v1.3.1/lib/liblivox_lidar_sdk_shared.so",
    root / "install/livox_ros_driver2-v1.2.6/lib/liblivox_ros_driver2.so",
    root / "install/robots_localization/lib/robots_localization/robots_localization_node",
]
patches = sorted((root / "patches").glob("roboparty-navigation-x5-*.patch"))
payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "result": "PASS" if all(path.is_file() for path in artifacts) else "FAIL",
    "scope": "project-only S100 build of official RoboParty FAST-LIO2 chain",
    "machine": platform.machine(),
    "source_lock": {
        "roboparty_navigation_commit": "d6ab9913599672af78422eb2e89a16c0460a8009",
        "robots_localization_commit": "dc4375a707dc4546ffad3a631f4e0d7c211c065b",
        "nlink_parser_commit": "1cc0a14e08d3ab1913056613eefbc6415d512698",
        "livox_ros_driver2_commit": "13eb05e4e6dd7a765b934d0c5fd6236676a57b49",
        "livox_sdk2_commit": "f5d9375f84efe2b15bc0a052d3e18482ed13adf4",
    },
    "patches": [
        {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in patches
    ],
    "artifacts": [
        {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in artifacts if path.is_file()
    ],
    "system_install_performed": False,
    "device_access": False,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["result"] == "PASS" else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
