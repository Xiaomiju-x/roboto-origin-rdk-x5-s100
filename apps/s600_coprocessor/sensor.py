"""Reuse previous tested device acquisition, extending only file-level text intents."""

import importlib.util
import runpy
import sys
from pathlib import Path

old = Path(__file__).resolve().parent / "acquisition"
spec = importlib.util.spec_from_file_location("core", old / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
previous = core.decide


def decide(text, confidence, observation):
    clean = "".join(text.split()).strip("，。！？,.!?")
    for name, cid in [("椅子", 56), ("人", 0), ("瓶子", 39)]:
        if (
            clean in [f"找到前面的{name}", f"找到{name}", f"寻找{name}"]
            and confidence >= 0.8
        ):
            return {
                "intent": "FIND_TARGET",
                "target": name,
                "class_id": cid,
                "confidence": confidence,
                "transcript": text,
                "motion_output": False,
            }
    result = previous(text, confidence, observation)
    result["transcript"] = text
    return result


core.decide = decide
sys.modules["core"] = core
runpy.run_path(str(old / "run.py"), run_name="__main__")
