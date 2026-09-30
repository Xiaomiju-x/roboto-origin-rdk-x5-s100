"""Select official runtime for local use and deduplicate identical library aliases."""

import argparse
import hashlib
import shutil
import tarfile
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    lib = args.output / "lib"
    lib.mkdir()
    known = {}
    for p in sorted((args.runtime / "lib").iterdir()):
        if p.is_file():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest in known:
                (lib / p.name).symlink_to(known[digest])
            else:
                shutil.copyfile(p, lib / p.name)
                known[digest] = p.name
    shutil.copytree(args.runtime / "examples/vlm_demo", args.output / "vlm_demo")
    shutil.copytree(
        args.runtime / "configs/Qwen3_VL_config", args.output / "Qwen3_VL_config"
    )
    shutil.copytree(
        args.runtime / "examples/whisper_demo", args.output / "whisper_demo"
    )
    shutil.copytree(
        args.runtime / "configs/Whisper_Medium_config",
        args.output / "Whisper_Medium_config",
    )
    archive = args.output.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as f:
        f.add(args.output, arcname=args.output.name)
    print(
        "runtime_pack_bytes",
        archive.stat().st_size,
        "sha256",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
    )


if __name__ == "__main__":
    main()
