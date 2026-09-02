#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
WHEEL="$ROOT/third_party/wheels/open3d-0.17.0-cp310-cp310-manylinux_2_27_aarch64.whl"
DEPS="$ROOT/third_party/archives/open3d-0.17.0-pydeps-cp310-aarch64.tar.gz"
TARGET="$ROOT/third_party/open3d_python"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

test "$(sha256sum "$WHEEL" | awk '{print $1}')" = \
  e28e515f4850380ab4f25842bce5f3d4c63b5565f9f8d2df141059b62250ff9e
test "$(sha256sum "$DEPS" | awk '{print $1}')" = \
  140fdc45255ea02e741e1510b07e6bf25df618ca538c6918cc7b6174ac773ff7

mkdir -p "$TARGET"
PIP_NO_CACHE_DIR=1 PYTHONNOUSERSITE=1 python3 -m pip install \
  --disable-pip-version-check --no-index --no-deps --upgrade \
  --target "$TARGET" "$WHEEL"
tar -xzf "$DEPS" -C "$TARGET"

PYTHONNOUSERSITE=1 PYTHONPATH="$TARGET:$ROOT/third_party/python" python3 - <<'PY'
import open3d
cloud = open3d.geometry.PointCloud()
assert len(cloud.points) == 0
assert open3d.__version__ == "0.17.0"
print("open3d", open3d.__version__)
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
