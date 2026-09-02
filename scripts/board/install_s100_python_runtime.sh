#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
WHEELS="$ROOT/third_party/wheels"
TARGET="$ROOT/third_party/python"
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

cd "$ROOT"
python3 - "$WHEELS" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = {
    "onnxruntime-1.21.0-cp310-cp310-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl": "635d4ab13ae0f150dd4c6ff8206fd58f1c6600636ecc796f6f0c42e4c918585b",
    "coloredlogs-15.0.1-py2.py3-none-any.whl": "612ee75c546f53e92e70049c9dbfcc18c935a2b9a53b66085ce9ef6a6e5c0934",
    "flatbuffers-25.2.10-py2.py3-none-any.whl": "ebba5f4d5ea615af3f7fd70fc310636fbb2bbd1f566ac0a23d98dd412de50051",
    "humanfriendly-10.0-py2.py3-none-any.whl": "1697e1a8a8f550fd43c2865cd84542fc175a61dcb779b6fee18cf6b6ccba1477",
    "mpmath-1.3.0-py3-none-any.whl": "a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c",
    "sympy-1.13.3-py3-none-any.whl": "54612cf55a62755ee71824ce692986f23c88ffa77207b30c1368eda4a7060f73",
}
for name, digest in expected.items():
    path = root / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit(f"hash mismatch: {name}: {actual} != {digest}")
    print(f"{name}: OK")
PY

mkdir -p "$TARGET"
PIP_NO_CACHE_DIR=1 PYTHONNOUSERSITE=1 python3 -m pip install \
  --disable-pip-version-check --no-index --no-deps --upgrade \
  --target "$TARGET" "$WHEELS"/*.whl

PYTHONNOUSERSITE=1 PYTHONPATH="$TARGET" python3 - <<'PY'
import onnxruntime
assert onnxruntime.__version__ == "1.21.0"
assert "CPUExecutionProvider" in onnxruntime.get_available_providers()
print("onnxruntime", onnxruntime.__version__, onnxruntime.get_available_providers())
PY

bash "$ROOT/scripts/verify_s100_safety.sh"
