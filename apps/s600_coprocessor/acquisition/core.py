"""Sensor-frame observations and text intents; never produces motion commands."""

import math


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (0x4D if value & 128 else 0)) & 255
    return value


def observe(points, stamp, now):
    if not math.isfinite(stamp) or not 0 <= now - stamp <= 0.5:
        return {"status": "STALE", "sectors": {}}
    sectors = {}
    for sector in range(4):
        values = [
            d
            for a, d, q in points
            if math.isfinite(a)
            and math.isfinite(d)
            and sector * 90 <= a < (sector + 1) * 90
            and 0.03 <= d <= 12
            and q > 0
        ]
        sectors[str(sector * 90)] = {
            "points": len(values),
            "nearest_m": min(values) if values else None,
        }
    return {
        "status": "OBSERVED"
        if any(s["points"] for s in sectors.values())
        else "NO_VALID_RETURN",
        "frame": "lidar_native_UNCALIBRATED",
        "sectors": sectors,
    }


def decide(text, confidence, observation):
    text = "".join(text.split()).strip("，。！？,.!?")
    intents = {
        "查询周围障碍": "QUERY",
        "周围有没有障碍": "QUERY",
        "开始任务": "START_SHADOW",
        "取消任务": "CANCEL_SHADOW",
        "查询状态": "STATUS",
    }
    intent = (
        intents.get(text) if math.isfinite(confidence) and confidence >= 0.8 else None
    )
    result = {"intent": intent or "REJECTED", "motion_output": False}
    if intent == "QUERY":
        result["observation"] = observation
        result["reply"] = (
            "雷达数据不可用"
            if observation["status"] != "OBSERVED"
            else "已读取雷达各角度区间距离；安装方向尚未标定"
        )
    elif intent:
        result["reply"] = {
            "START_SHADOW": "演示任务已开始",
            "CANCEL_SHADOW": "演示任务已取消",
            "STATUS": "当前为传感器演示模式",
        }[intent]
    return result
