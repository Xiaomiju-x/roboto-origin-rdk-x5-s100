#!/usr/bin/env python3
"""Fail-closed checks for the sanitized public source tree."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT_SUFFIXES = {
    "",
    ".cff",
    ".cpp",
    ".in",
    ".json",
    ".html",
    ".lock",
    ".md",
    ".patch",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_BINARY_SUFFIXES = {".bin", ".hbm", ".mcap", ".onnx", ".p12", ".pfx", ".pt", ".pth"}
MAX_FILE_BYTES = 25 * 1024 * 1024

PATTERNS = {
    "private IPv4 address": re.compile(
        r"(?<!\d)(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\d)"
    ),
    "personal Windows path": re.compile(r"(?i)\bD:[\\/](?:rdk|WorkData)\b"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "credential assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9/+_.-]{8,}"
    ),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(
            part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts
        )
    )


def main() -> int:
    errors: list[str] = []
    candidates = files()

    for path in candidates:
        rel = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"oversized file: {rel}")
        if path.suffix.lower() in FORBIDDEN_BINARY_SUFFIXES:
            errors.append(f"forbidden binary artifact: {rel}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"non-UTF-8 public text: {rel}")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label}: {rel}")

        if path.suffix.lower() == ".md":
            for raw_target in MARKDOWN_LINK.findall(text):
                target = raw_target.strip().strip("<>").split("#", 1)[0]
                if not target or re.match(r"^(?:https?://|mailto:)", target):
                    continue
                if not (path.parent / target).resolve().exists():
                    errors.append(f"broken local Markdown link: {rel} -> {raw_target}")

        try:
            if path.suffix.lower() == ".json":
                json.loads(text)
            elif path.suffix.lower() in {".yaml", ".yml"}:
                yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            errors.append(f"invalid structured data: {rel}: {exc}")

    required = [
        "README.md",
        "README.en.md",
        "LICENSE",
        "NOTICE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/RESULTS.md",
        "docs/SAFETY.md",
        "docs/ROADMAP.md",
    ]
    for rel in required:
        if not (ROOT / rel).is_file():
            errors.append(f"missing required file: {rel}")

    if errors:
        print("Public release audit FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Public release audit PASS: {len(candidates)} files checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
