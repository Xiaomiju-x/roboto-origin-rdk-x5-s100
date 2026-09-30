"""Fetch published S600 models, checking exact size and SHA256 before inference."""

import argparse
import hashlib
import urllib.request
from pathlib import Path

MODELS = [
    (
        "models_qwen_ram",
        "Qwen3-VL-2B-Instruct/w4",
        "Qwen3-VL-2B-Instruct_language_chunk_512_cache_1024_w4_nash-p_corenum_4_4.hbm",
        1163859776,
        "04358614746e00332f9eaa28d9d9828a5ae7b2eb02189d0f921b83b7b635d5a4",
    ),
    (
        "models_qwen_ram",
        "Qwen3-VL-2B-Instruct/w4",
        "Qwen3-VL-2B-Instruct_vision_448x448_w8-4_nash-p_corenum_4.hbm",
        470302800,
        "d8ba15df476f1d82cac2790b9ea2991b28192e5ac4c1b83312e3c73f64022ac3",
    ),
    (
        "models_qwen_ram",
        "Qwen3-VL-2B-Instruct/w4",
        "Qwen3-VL-2B-Instruct_embed_tokens_w4_fp16.bin",
        622329856,
        "54cc159afd0cd4cdd70ce13e9995e35525dd5b13f23edc482d284412e68bf03b",
    ),
    (
        "models_whisper_ram",
        "whisper-medium/w8",
        "whisper-medium_audio_encode_duration_30s_sr_16k_w8_nash-p_corenum_4.hbm",
        398696576,
        "9c97029ad11e8b4576166492abbe2975f05411750ff15907af8e3ae3296458e1",
    ),
    (
        "models_whisper_ram",
        "whisper-medium/w8",
        "whisper-medium_audio_decode_w8_nash-p_corenum_1_1.hbm",
        711859496,
        "ba41d112c5ca5c213cc72d90c2e396a574cd3cbc895d865cdfc7ee46f8f7c80f",
    ),
]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(1048576):
            h.update(data)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    for directory, remote, name, size, sha in MODELS:
        path = args.root / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size == size and digest(path) == sha:
            print("VERIFIED", name, flush=True)
            continue
        if args.verify_only:
            raise RuntimeError("model missing/incomplete/hash mismatch: " + name)
        partial = path.with_suffix(path.suffix + ".partial")
        url = (
            "https://d-robotics-aitoolchain.oss-cn-beijing.aliyuncs.com/llm_s600/1.0.5/models/"
            + remote
            + "/"
            + name
        )
        with (
            urllib.request.urlopen(url, timeout=60) as response,
            partial.open("wb") as f,
        ):
            received = 0
            while data := response.read(1048576):
                received += len(data)
                if received > size:
                    raise RuntimeError("download exceeds recorded size")
                f.write(data)
        if received != size or digest(partial) != sha:
            raise RuntimeError("download validation failed: " + name)
        partial.replace(path)
        print("FETCHED", name, flush=True)


if __name__ == "__main__":
    main()
