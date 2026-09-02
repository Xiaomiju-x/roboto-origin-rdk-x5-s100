#!/usr/bin/env python3
"""Convert normalized detector JSON into a Roboto Origin shadow event."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from roboto_upgrade.yolo_shadow import make_shadow_decision, normalize_pixel_detections


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=("x5", "s100", "s600", "host"), required=True)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    detections = normalize_pixel_detections(
        payload["detections"],
        image_width=int(payload["image_width"]),
        image_height=int(payload["image_height"]),
    )
    result = make_shadow_decision(
        detections,
        platform=args.platform,
        frame_id=int(payload.get("frame_id", 0)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"event": result["event"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
