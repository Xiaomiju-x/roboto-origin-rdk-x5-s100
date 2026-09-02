#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
SOURCE="$ROOT/src/official/roboparty_deploy/src/camera"
BUILD="$ROOT/build/official_depth"
INSTALL="$ROOT/install/official_depth"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

test -f "$SOURCE/CMakeLists.txt"
test "$(sha256sum "$SOURCE/models/encoder.onnx" | awk '{print $1}')" = \
  c6ffe32b4b72f17736a7dfed186d53dc14b51a3b3dbad72f833c98d4e616d705
test "$(sha256sum "$SOURCE/thirdparty/onnxruntime-linux-aarch64-1.21.0.tgz" | awk '{print $1}')" = \
  4508084bde1232ee1ab4b6fad2155be0ea2ccab1c1aae9910ddb3fb68a60805e

mkdir -p "$BUILD" "$INSTALL" "$ROOT/logs/build"
export PATH="$ROOT/third_party/bin:$PATH"
export CMAKE_BUILD_PARALLEL_LEVEL=2
cmake -S "$SOURCE" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL" \
  -DBUILD_TESTING=OFF \
  | tee "$ROOT/logs/build/official_depth_configure.log"
cmake --build "$BUILD" --parallel 2 \
  | tee "$ROOT/logs/build/official_depth_build.log"
cmake --install "$BUILD" \
  | tee "$ROOT/logs/build/official_depth_install.log"

test -x "$INSTALL/lib/camera/depth_node"
python3 - "$SOURCE" "$INSTALL" "$ROOT/evidence/d2_depth_build_receipt.json" <<'PY'
import datetime
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

source, install, output = map(pathlib.Path, sys.argv[1:])
binary = install / "lib/camera/depth_node"

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

payload = {
    "schema_version": 1,
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "result": "PASS",
    "scope": "project-only S100 build of official depth pipeline",
    "machine": platform.machine(),
    "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
    "source_cmake_sha256": sha256(source / "CMakeLists.txt"),
    "model_sha256": sha256(source / "models/encoder.onnx"),
    "binary": {"path": str(binary), "bytes": binary.stat().st_size, "sha256": sha256(binary)},
    "system_install_performed": False,
    "device_access": False,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
