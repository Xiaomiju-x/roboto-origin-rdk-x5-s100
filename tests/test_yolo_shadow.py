import pytest

from roboto_upgrade.yolo_shadow import Detection, ShadowConfig, make_shadow_decision, normalize_pixel_detections


def test_zone_hit_is_shadow_only() -> None:
    decision = make_shadow_decision(
        [Detection(class_id=0, score=0.91, x1=0.3, y1=0.4, x2=0.6, y2=0.9)],
        platform="x5",
        frame_id=7,
    )
    assert decision["event"] == "STOP_CANDIDATE"
    assert decision["sink"] == "file_only"
    assert decision["motion_command_emitted"] is False


def test_outside_or_irrelevant_detection_is_clear() -> None:
    detections = [
        Detection(class_id=0, score=0.9, x1=0.0, y1=0.0, x2=0.1, y2=0.1),
        Detection(class_id=14, score=0.9, x1=0.3, y1=0.5, x2=0.5, y2=0.8),
    ]
    assert make_shadow_decision(detections, platform="s600", frame_id=0)["event"] == "CLEAR"


def test_pixel_normalization_and_clipping() -> None:
    detections = normalize_pixel_detections(
        [{"class_id": 5, "score": 0.8, "x1": -2, "y1": 25, "x2": 120, "y2": 110}],
        image_width=100,
        image_height=100,
    )
    assert detections == [Detection(class_id=5, score=0.8, x1=0.0, y1=0.25, x2=1.0, y2=1.0)]


@pytest.mark.parametrize(
    "detection",
    [
        Detection(class_id=0, score=1.1, x1=0.1, y1=0.1, x2=0.2, y2=0.2),
        Detection(class_id=0, score=0.9, x1=0.2, y1=0.1, x2=0.2, y2=0.3),
        Detection(class_id=-1, score=0.9, x1=0.1, y1=0.1, x2=0.2, y2=0.3),
    ],
)
def test_invalid_detection_fails_closed(detection: Detection) -> None:
    with pytest.raises(ValueError):
        make_shadow_decision([detection], platform="host", frame_id=0)


def test_invalid_zone_fails_closed() -> None:
    with pytest.raises(ValueError):
        make_shadow_decision(
            [],
            config=ShadowConfig(zone_x1=0.8, zone_x2=0.2),
            platform="host",
            frame_id=0,
        )
