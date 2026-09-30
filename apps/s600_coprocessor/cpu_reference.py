"""FP32 Ultralytics reference on S600 CPU; architecture match, not verified weight parity."""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
os.environ["YOLO_CONFIG_DIR"] = str(root / "cpu_config")
os.environ["MPLCONFIGDIR"] = str(root / "cpu_config/matplotlib")
os.environ["XDG_CACHE_HOME"] = str(root / "cache")
# Only load the explicitly downloaded official Ultralytics weight in this process.
os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
sys.path.insert(0, str(root / "cpu_deps"))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402 -- set isolated cache/config paths before importing the runtime
from ultralytics import YOLO  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(4)
    model = YOLO(args.model)
    image = cv2.imread(args.image)
    if image is None:
        raise ValueError("image")
    for _ in range(3):
        model.predict(image, device="cpu", imgsz=640, rect=False, verbose=False)
    durations = []
    start = time.perf_counter()
    result = None
    while time.perf_counter() - start < args.seconds:
        t = time.perf_counter()
        result = model.predict(
            image, device="cpu", imgsz=640, rect=False, verbose=False
        )
        durations.append((time.perf_counter() - t) * 1000)
    record = {
        "backend": "PyTorch_CPU_FP32",
        "torch": torch.__version__,
        "threads": 4,
        "frames": len(durations),
        "elapsed_s": time.perf_counter() - start,
        "p50_ms": float(np.percentile(durations, 50)),
        "p95_ms": float(np.percentile(durations, 95)),
        "model_sha256": hashlib.sha256(Path(args.model).read_bytes()).hexdigest(),
        "comparability": "same_family_scale_task; HBM source weight parity unverified; different implementation/precision",
        "input_shape": list(image.shape),
        "model_input": [1, 3, 640, 640],
        "parameters": sum(p.numel() for p in model.model.parameters()),
        "objects": [
            {"class_id": int(b.cls), "score": float(b.conf), "box": b.xyxy[0].tolist()}
            for b in result[0].boxes
        ],
    }
    cv2.imwrite(str(args.output / "reference.jpg"), result[0].plot())
    (args.output / "summary.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


if __name__ == "__main__":
    main()
