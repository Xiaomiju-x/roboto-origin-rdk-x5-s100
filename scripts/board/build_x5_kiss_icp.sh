#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project
SOURCE="$ROOT/src/roboto_origin_upgrade/upstream/kiss-icp-v1.3.0"
BUILD="$ROOT/build/roboto_origin_upgrade/kiss_icp_v130_sophus122"
INSTALL="$ROOT/install/roboto_origin_upgrade/kiss_icp_v130"
LOG="$ROOT/log/roboto_origin_upgrade/kiss_icp_v130_sophus122"
SOPHUS_PREFIX="$ROOT/roboto_origin/deps/install"
RECEIPT="$ROOT/logs/roboto_origin_upgrade/build/kiss_icp_v130.json"
EXPECTED=b16835283aee62f7d5e2bdf6c1c3bb2930de74ff

cd "$ROOT"
source "$ROOT/env.sh"
set +u
source /opt/tros/humble/setup.bash
set -u

test "${ROS_DOMAIN_ID:-}" = 42
test -d "$SOURCE/.git"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$EXPECTED"
test -z "$(git -C "$SOURCE" status --porcelain)"
test -f "$SOPHUS_PREFIX/share/sophus/cmake/SophusConfig.cmake"
mkdir -p "$BUILD" "$INSTALL" "$LOG"

export CMAKE_PREFIX_PATH="$SOPHUS_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export Sophus_DIR="$SOPHUS_PREFIX/share/sophus/cmake"
export CMAKE_BUILD_PARALLEL_LEVEL=2
colcon --log-base "$LOG" build \
  --base-paths "$SOURCE/ros" \
  --build-base "$BUILD" \
  --install-base "$INSTALL" \
  --parallel-workers 1 \
  --event-handlers console_direct+ \
  --cmake-args \
    -DCMAKE_BUILD_TYPE=Release \
    -DUSE_SYSTEM_SOPHUS=ON \
    -DUSE_SYSTEM_TSL-ROBIN-MAP=OFF \
    -DUSE_SYSTEM_TBB=ON

test -f "$INSTALL/setup.bash"
set +u
source "$INSTALL/setup.bash"
set -u
ros2 pkg prefix kiss_icp
test -z "$(git -C "$SOURCE" status --porcelain)"

mkdir -p "$(dirname "$RECEIPT")"
python3 - "$SOURCE" "$INSTALL" "$SOPHUS_PREFIX" "$RECEIPT" <<'PY'
import hashlib, json, pathlib, platform, re, subprocess, sys
source, install, sophus_prefix, receipt = map(pathlib.Path, sys.argv[1:])
def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()
def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()
binary = install / "kiss_icp/lib/kiss_icp/kiss_icp_node"
library = install / "kiss_icp/lib/libodometry_component.so"
version_text = (sophus_prefix / "share/sophus/cmake/SophusConfigVersion.cmake").read_text()
match = re.search(r'set\(PACKAGE_VERSION "([^"]+)"\)', version_text)
payload = {
    "schema_version": 1,
    "result": "PASS",
    "scope": "isolated X5 ROS 2 build; no devices or system installation",
    "machine": platform.machine(),
    "source": {
        "path": str(source),
        "commit": command("git", "-C", str(source), "rev-parse", "HEAD"),
        "clean": command("git", "-C", str(source), "status", "--porcelain") == "",
    },
    "toolchain": {
        "cmake": command("cmake", "--version").splitlines()[0],
        "compiler": command("g++", "--version").splitlines()[0],
        "sophus": match.group(1) if match else "unknown",
        "sophus_origin": str(sophus_prefix),
    },
    "cmake_options": {
        "USE_SYSTEM_SOPHUS": True,
        "USE_SYSTEM_TSL_ROBIN_MAP": False,
        "USE_SYSTEM_TBB": True,
        "CMAKE_BUILD_TYPE": "Release",
    },
    "artifacts": [
        {"path": str(binary), "bytes": binary.stat().st_size, "sha256": digest(binary)},
        {"path": str(library), "bytes": library.stat().st_size, "sha256": digest(library)},
    ],
}
receipt.write_text(json.dumps(payload, indent=2) + "\n")
print(receipt)
PY
