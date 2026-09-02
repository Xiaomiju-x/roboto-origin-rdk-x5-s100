import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "summaries"


def load(name: str) -> dict:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def test_all_board_results_are_real_bpu_file_only() -> None:
    expected = {
        "x5_yolo_shadow_summary.json": "X5_YOLO26_BYTETRACK_SHADOW_FILE_PASS",
        "s100_yolo11_shadow_summary.json": "S100_YOLO11_BPU_SHADOW_FILE_PASS",
        "s600_yolo11_shadow_summary.json": "S600_YOLO11_BPU_SHADOW_FILE_PASS",
    }
    for name, status in expected.items():
        payload = load(name)
        assert payload["status"] == status
        assert payload["claims"]["real_bpu_inference"] is True
        assert payload["claims"]["file_input_file_sink"] is True
        assert payload["claims"]["motion_command_emitted"] is False
        assert payload["claims"]["physical_robot_validated"] is False
        assert payload["shadow_decision"]["event"] == "STOP_CANDIDATE"


def test_s100_s600_functional_and_strict_results_stay_distinct() -> None:
    comparison = load("s100_s600_yolo11_comparison.json")
    assert comparison["status"] == "S100_S600_YOLO11_SHADOW_FUNCTIONAL_PARITY_PASS"
    assert comparison["strict_numeric_status"] == "S100_S600_YOLO11_STRICT_SCORE_PARITY_FAIL"
    assert comparison["box_iou"]["min"] >= comparison["gates"]["min_box_iou"]
    assert comparison["score_abs_delta"]["max"] > comparison["gates"]["max_score_abs_delta"]
    assert comparison["decision_equal"] is True
