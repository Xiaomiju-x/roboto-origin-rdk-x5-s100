"""RTX5090 float reference and official ten-output YOLO11-Seg ONNX export."""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", type=Path, required=True)
    ap.add_argument("--weight", type=Path, required=True)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "config").mkdir()
    (args.output / "matplotlib").mkdir()
    os.environ["YOLO_CONFIG_DIR"] = str(args.output / "config")
    os.environ["MPLCONFIGDIR"] = str(args.output / "matplotlib")
    import cv2
    import numpy as np
    import onnxruntime as ort
    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required; no CPU fallback")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    sys.path.insert(0, str(args.upstream.resolve()))
    pre_spec = importlib.util.spec_from_file_location(
        "official_preprocess", args.upstream / "utils/py_utils/preprocess.py"
    )
    preprocess = importlib.util.module_from_spec(pre_spec)
    pre_spec.loader.exec_module(preprocess)
    spec = importlib.util.spec_from_file_location(
        "official_export",
        args.upstream
        / "samples/vision/ultralytics_yolo/conversion/export_monkey_patch.py",
    )
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    model = YOLO(str(args.weight)).model.eval().cuda()
    exporter.modelZooOptimizer(model.model)
    image = cv2.imread(str(args.image))
    resized = preprocess.resized_image(image, 640, 640, 1)
    y, uv = preprocess.bgr_to_nv12_planes(resized)
    # Compare from the NV12 representation supplied to the board, avoiding an unrecorded input difference.
    nv12 = np.concatenate([y.reshape(-1), uv.reshape(-1)]).reshape(960, 640)
    rgb = cv2.cvtColor(nv12, cv2.COLOR_YUV2RGB_NV12)
    data = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None]).astype(np.float32) / 255
    tensor = torch.from_numpy(data).cuda()
    with torch.inference_mode():
        for _ in range(5):
            model(tensor)
        torch.cuda.synchronize()
        timings = []
        for _ in range(30):
            start = time.perf_counter()
            raw = model(tensor)
            torch.cuda.synchronize()
            timings.append((time.perf_counter() - start) * 1000)
        golden = [a.detach().cpu().numpy() for a in raw]
    names = [f"head_{i}" for i in range(10)]
    onnx_path = args.output / "yolo11x_seg_float.onnx"
    torch.onnx.export(
        model,
        tensor,
        str(onnx_path),
        input_names=["images"],
        output_names=names,
        opset_version=19,
        dynamo=False,
    )
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    session = ort.InferenceSession(
        str(onnx_path), opts, providers=["CPUExecutionProvider"]
    )
    reference = session.run(None, {"images": data})
    checks = []
    for i, (a, b) in enumerate(zip(golden, reference)):
        af = a.astype(np.float64).ravel()
        bf = b.astype(np.float64).ravel()
        cosine = float(np.dot(af, bf) / (np.linalg.norm(af) * np.linalg.norm(bf)))
        rel = float(np.linalg.norm(af - bf) / max(np.linalg.norm(af), 1e-12))
        checks.append(
            {
                "output": i,
                "shape": list(a.shape),
                "max_abs": float(np.max(np.abs(af - bf))),
                "cosine": cosine,
                "relative_l2": rel,
                "finite": bool(np.isfinite(b).all()),
                "pass": cosine >= 0.99999
                and rel <= 0.001
                and bool(np.isfinite(b).all()),
            }
        )
    np.savez_compressed(
        args.output / "float_outputs.npz", **{names[i]: a for i, a in enumerate(golden)}
    )
    np.save(args.output / "input.npy", data)
    record = {
        "device": torch.cuda.get_device_name(0),
        "tensor_device": str(tensor.device),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "sm": list(torch.cuda.get_device_capability(0)),
        "source_weight_sha256": hashlib.sha256(args.weight.read_bytes()).hexdigest(),
        "image_sha256": hashlib.sha256(args.image.read_bytes()).hexdigest(),
        "gpu_forward_p50_ms": float(np.percentile(timings, 50)),
        "gpu_forward_p95_ms": float(np.percentile(timings, 95)),
        "onnx_sha256": hashlib.sha256(onnx_path.read_bytes()).hexdigest(),
        "onnx_output_checks": checks,
        "export_validation_pass": all(c["pass"] for c in checks),
        "training_performed": False,
        "hbm_source_weight_parity_verified": False,
    }
    (args.output / "summary.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
    if not record["export_validation_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
