"""Bounded official Whisper-medium inference on S600 BPU. No training."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path


def parse_transcription(stdout):
    clean = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
    if "[Transcription] " not in clean:
        raise RuntimeError("no transcription")
    text = (
        clean.split("[Transcription] ", 1)[1]
        .split("[Performance]")[0]
        .split("=====")[0]
        .strip()
    )
    return text, re.findall(r"\[Performance\]\s*([^\n]+)", clean)


def transcribe(root, audio, output):
    root = Path(root).resolve()
    audio = Path(audio).resolve()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(
        LD_LIBRARY_PATH=str(root / "runtime_compact/lib"),
        HB_DNN_USER_DEFINED_L2M_SIZES="6:6:6:6",
    )
    start = time.monotonic()
    p = subprocess.run(
        [
            str(root / "runtime_compact/whisper_demo/whisper"),
            "--config_path",
            str(root / "whisper_zh.json"),
            "--audio_path",
            str(audio),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=90,
    )
    (output / "native.stdout").write_text(p.stdout)
    (output / "native.stderr").write_text(p.stderr)
    if p.returncode:
        raise RuntimeError(f"Whisper exit {p.returncode}; inspect logs")
    text, metrics = parse_transcription(p.stdout)
    result = {
        "model": "Whisper-medium",
        "backend": "S600_BPU_OELLM_1.0.5",
        "transcript": text,
        "wall_seconds_including_load": time.monotonic() - start,
        "metrics_native": metrics,
        "training_performed": False,
        "motion_output": False,
    }
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    print(
        json.dumps(transcribe(args.root, args.audio, args.output), ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
