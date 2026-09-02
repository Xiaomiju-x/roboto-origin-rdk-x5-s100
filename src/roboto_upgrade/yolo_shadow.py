"""Board-neutral, file-only safety interpretation for YOLO detections.

The module deliberately produces observations, never robot commands.  It is
small enough to run unchanged on RDK X5, S100, and S600 after a board-specific
detector converts its output to the normalized detection contract below.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class Detection:
    """One normalized image-space detection."""

    class_id: int
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    def validate(self) -> None:
        values = (self.score, self.x1, self.y1, self.x2, self.y2)
        if not all(isinstance(value, (int, float)) for value in values):
            raise ValueError("detection values must be numeric")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be in [0, 1]")
        if not (0.0 <= self.x1 < self.x2 <= 1.0 and 0.0 <= self.y1 < self.y2 <= 1.0):
            raise ValueError("box must be finite, normalized, and have positive area")
        if self.class_id < 0:
            raise ValueError("class_id must be non-negative")


@dataclass(frozen=True)
class ShadowConfig:
    """Conservative image-space zone used only for a shadow decision."""

    score_threshold: float = 0.35
    zone_x1: float = 0.20
    zone_y1: float = 0.45
    zone_x2: float = 0.80
    zone_y2: float = 1.00
    obstacle_class_ids: tuple[int, ...] = (0, 1, 2, 3, 5, 6, 7)

    def validate(self) -> None:
        if not 0.0 < self.score_threshold <= 1.0:
            raise ValueError("score_threshold must be in (0, 1]")
        if not (0.0 <= self.zone_x1 < self.zone_x2 <= 1.0):
            raise ValueError("invalid horizontal zone")
        if not (0.0 <= self.zone_y1 < self.zone_y2 <= 1.0):
            raise ValueError("invalid vertical zone")


def _intersection_over_detection(det: Detection, cfg: ShadowConfig) -> float:
    left = max(det.x1, cfg.zone_x1)
    top = max(det.y1, cfg.zone_y1)
    right = min(det.x2, cfg.zone_x2)
    bottom = min(det.y2, cfg.zone_y2)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area = (det.x2 - det.x1) * (det.y2 - det.y1)
    return intersection / area


def make_shadow_decision(
    detections: Iterable[Detection],
    *,
    config: ShadowConfig | None = None,
    platform: str,
    frame_id: int,
) -> dict:
    """Return a serializable file-sink observation with no actuation surface."""

    cfg = config or ShadowConfig()
    cfg.validate()
    if platform not in {"x5", "s100", "s600", "host"}:
        raise ValueError("platform must be x5, s100, s600, or host")
    if frame_id < 0:
        raise ValueError("frame_id must be non-negative")

    accepted: list[tuple[Detection, float]] = []
    all_detections = list(detections)
    for detection in all_detections:
        detection.validate()
        overlap = _intersection_over_detection(detection, cfg)
        if (
            detection.score >= cfg.score_threshold
            and detection.class_id in cfg.obstacle_class_ids
            and overlap > 0.0
        ):
            accepted.append((detection, overlap))

    accepted.sort(key=lambda item: (-item[0].score, -item[1], item[0].class_id))
    event = "STOP_CANDIDATE" if accepted else "CLEAR"
    return {
        "schema": "roboto_origin.yolo_shadow.v1",
        "platform": platform,
        "frame_id": frame_id,
        "event": event,
        "sink": "file_only",
        "shadow_only": True,
        "motion_command_emitted": False,
        "detection_count": len(all_detections),
        "zone_hit_count": len(accepted),
        "zone": {
            "x1": cfg.zone_x1,
            "y1": cfg.zone_y1,
            "x2": cfg.zone_x2,
            "y2": cfg.zone_y2,
        },
        "top_zone_hit": (
            {
                **asdict(accepted[0][0]),
                "intersection_over_detection": accepted[0][1],
            }
            if accepted
            else None
        ),
    }


def normalize_pixel_detections(
    rows: Iterable[dict], *, image_width: int, image_height: int
) -> list[Detection]:
    """Convert pixel-space detector output into the normalized contract."""

    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    result = []
    for row in rows:
        result.append(
            Detection(
                class_id=int(row["class_id"]),
                score=float(row["score"]),
                x1=max(0.0, min(1.0, float(row["x1"]) / image_width)),
                y1=max(0.0, min(1.0, float(row["y1"]) / image_height)),
                x2=max(0.0, min(1.0, float(row["x2"]) / image_width)),
                y2=max(0.0, min(1.0, float(row["y2"]) / image_height)),
            )
        )
    return result
