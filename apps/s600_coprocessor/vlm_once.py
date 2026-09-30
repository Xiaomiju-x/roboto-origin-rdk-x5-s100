"""Bounded wrapper around official S600 Qwen3-VL runtime. Produces answers, never actions."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path


def parse_answer(stdout):
    clean = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
    if "[Assistant] >>> " not in clean:
        raise RuntimeError("no assistant response; inspect native log")
    answer = clean.split("[Assistant] >>> ", 1)[1].split("=====")[0].strip()
    # Prefix/suffix are native demo formatting, not independent generated answers.
    return answer, clean


def infer(root, image, prompt, output):
    root = Path(root).resolve()
    image = Path(image).resolve()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    if not image.is_file():
        raise ValueError("image not available")
    if not prompt.strip() or len(prompt) > 300 or "\n" in prompt:
        raise ValueError("prompt length/newline")
    environment = os.environ.copy()
    environment.update(
        LD_LIBRARY_PATH=str(root / "runtime_compact/lib"),
        HB_DNN_USER_DEFINED_L2M_SIZES="6:6:6:6",
    )
    start = time.monotonic()
    result = subprocess.run(
        [
            str(root / "runtime_compact/vlm_demo/vlm"),
            "-c",
            str(root / "qwen3vl_2b_w4.json"),
            "-i",
            str(image),
        ],
        input=prompt + "\nexit\n",
        capture_output=True,
        text=True,
        env=environment,
        timeout=90,
        cwd=root,
    )
    (output / "native.stdout").write_text(result.stdout)
    (output / "native.stderr").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"VLM exit {result.returncode}; native logs retained")
    answer, clean = parse_answer(result.stdout)
    record = {
        "model": "Qwen3-VL-2B-Instruct",
        "backend": "S600_BPU_OELLM_1.0.5",
        "prompt": prompt,
        "answer": answer,
        "wall_seconds_including_load": time.monotonic() - start,
        "motion_output": False,
        "metrics_native": re.findall(r"===== ([^\n]+)", clean),
        "generated_text_is_not_geometry": True,
    }
    (output / "summary.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2)
    )
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", default="请用一句中文描述图中的主要物体，不推测距离。")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    print(
        json.dumps(
            infer(args.root, args.image, args.prompt, args.output), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
