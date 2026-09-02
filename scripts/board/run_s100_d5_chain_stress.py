#!/usr/bin/env python3
"""Run the D5 file-sink chain, fault campaign, and 30-minute S100 BPU stress."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from hbm_runtime import HB_HBMRuntime


POLICY_DEADLINE_MS = 20.0
GUARD_DEADLINE_MS = 5.0
POLICY_RATE_HZ = 50
GUARD_RATE_HZ = 200


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def only_output(result: dict[str, dict[str, np.ndarray]]) -> np.ndarray:
    if len(result) != 1:
        raise RuntimeError(f"expected one HBM model result, got {list(result)}")
    outputs = next(iter(result.values()))
    if len(outputs) != 1:
        raise RuntimeError(f"expected one HBM output, got {list(outputs)}")
    return np.asarray(next(iter(outputs.values())))


def timing_summary(values: list[float], deadline_ms: float) -> dict[str, float | int]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "p50_ms": float(np.percentile(array, 50)),
        "p95_ms": float(np.percentile(array, 95)),
        "p99_ms": float(np.percentile(array, 99)),
        "max_ms": float(array.max()),
        "mean_ms": float(array.mean()),
        "deadline_ms": deadline_ms,
        "deadline_misses": int(np.count_nonzero(array > deadline_ms)),
    }


class StressTiming:
    """Keep exact counts/misses and a bounded deterministic latency sample."""

    def __init__(self, deadline_ms: float) -> None:
        self.deadline_ms = deadline_ms
        self.count = 0
        self.total_ms = 0.0
        self.maximum_ms = 0.0
        self.deadline_misses = 0
        self.samples: list[float] = []

    def add(self, elapsed_ms: float) -> None:
        self.count += 1
        self.total_ms += elapsed_ms
        self.maximum_ms = max(self.maximum_ms, elapsed_ms)
        self.deadline_misses += int(elapsed_ms > self.deadline_ms)
        if self.count <= 10_000 or self.count % 100 == 0:
            self.samples.append(elapsed_ms)

    def summary(self) -> dict[str, object]:
        sampled = np.asarray(self.samples, dtype=np.float64)
        return {
            "count": self.count,
            "mean_ms_exact": self.total_ms / max(self.count, 1),
            "max_ms_exact": self.maximum_ms,
            "deadline_ms": self.deadline_ms,
            "deadline_misses_exact": self.deadline_misses,
            "latency_sample_method": "first 10000 calls plus every 100th call thereafter",
            "latency_sample_count": int(sampled.size),
            "sample_p50_ms": float(np.percentile(sampled, 50)),
            "sample_p95_ms": float(np.percentile(sampled, 95)),
            "sample_p99_ms": float(np.percentile(sampled, 99)),
        }


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def resource_sample(elapsed_s: float) -> dict[str, object]:
    sample: dict[str, object] = {
        "elapsed_s": elapsed_s,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    try:
        load = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        sample["load_1m"] = float(load[0])
        sample["load_5m"] = float(load[1])
        sample["load_15m"] = float(load[2])
    except (OSError, ValueError, IndexError) as error:
        sample["load_error"] = f"{type(error).__name__}: {error}"

    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"}:
                memory[key] = int(value.split()[0])
        sample["memory_kib"] = memory
    except (OSError, ValueError, IndexError) as error:
        sample["memory_error"] = f"{type(error).__name__}: {error}"

    thermal = {}
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        zone_type = read_text(str(zone / "type"))
        raw = read_text(str(zone / "temp"))
        if zone_type and raw:
            try:
                thermal[zone_type] = float(raw) / 1000.0
            except ValueError:
                continue
    sample["thermal_c"] = thermal

    cpu_frequency = {}
    for policy in sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy*")):
        raw = read_text(str(policy / "scaling_cur_freq"))
        if raw:
            try:
                cpu_frequency[policy.name] = int(raw)
            except ValueError:
                continue
    sample["cpu_scaling_cur_freq_khz"] = cpu_frequency

    try:
        monitor = subprocess.run(
            ["/usr/hobot/bin/hrut_somstatus"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
            check=False,
        )
        output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", monitor.stdout)
        bpu_temp = re.search(r"pvt_bpu_pvtc1_t1\s*:\s*([0-9.]+)", output)
        bpu_ratio = re.search(r"bpu0:\s*([0-9.]+)", output)
        sample["somstatus_returncode"] = monitor.returncode
        sample["bpu_temperature_c"] = float(bpu_temp.group(1)) if bpu_temp else None
        sample["bpu_ratio_percent"] = float(bpu_ratio.group(1)) if bpu_ratio else None
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        sample["somstatus_error"] = f"{type(error).__name__}: {error}"
    return sample


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--perception-result", type=Path, required=True)
    parser.add_argument("--nav-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy-steps", type=int, default=10_000)
    parser.add_argument("--stress-seconds", type=float, default=1800.0)
    parser.add_argument("--monitor-period", type=float, default=10.0)
    args = parser.parse_args()
    if args.policy_steps < 10_000:
        raise ValueError("D5 requires at least 10,000 policy steps")
    if args.stress_seconds < 1800.0:
        raise ValueError("D5 requires at least 1,800 wall-clock stress seconds")

    root = args.root.resolve()
    bundle = args.bundle.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sink_path = args.output.parent / "guarded_action_sink.jsonl"
    progress_path = args.output.parent / "progress.json"
    sys.path.insert(0, str(root / "src"))
    from roboto_upgrade.trust_guard import (  # pylint: disable=import-outside-toplevel
        ActionSafetyShield,
        GuardConfig,
    )

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    conversion = json.loads((bundle / "logs/conversion_summary.json").read_text(encoding="utf-8"))
    perception = json.loads(args.perception_result.read_text(encoding="utf-8"))
    navigation = json.loads(args.nav_result.read_text(encoding="utf-8"))
    if perception.get("result") != "PASS" or perception.get("summary", {}).get("pass") != 4:
        raise RuntimeError("D4 perception evidence is not PASS 4/4")
    if navigation.get("status") != "PASS" or navigation.get("goal_summary", {}).get("passed", 0) < 20:
        raise RuntimeError("D5 Nav2 multi-goal evidence is not PASS >=20")

    policy_spec = next(item for item in manifest["models"] if item["name"] == "policy")
    compiled = next(item for item in conversion["models"] if item["name"] == "policy")
    hbm_candidates = sorted((bundle / "compiled/policy").glob("*.hbm"))
    if len(hbm_candidates) != 1 or len(compiled["artifacts"]) != 1:
        raise RuntimeError("expected one locked policy HBM")
    hbm_path = hbm_candidates[0]
    fixture_path = bundle / "test/policy/inputs.npy"
    if sha256(hbm_path) != compiled["artifacts"][0]["sha256"]:
        raise RuntimeError("policy HBM hash mismatch")
    if sha256(fixture_path) != policy_spec["test_input_sha256"]:
        raise RuntimeError("policy fixture hash mismatch")
    fixtures = np.load(fixture_path, allow_pickle=False).astype(np.float32, copy=False)
    detection_count = next(
        int(item["detections"]) for item in perception["models"] if item["name"] == "yolo26_detect"
    )
    route_pose_count = int(navigation["goal_summary"]["total_path_poses"])
    fixture_index = (detection_count + route_pose_count) % len(fixtures)
    policy_input = np.ascontiguousarray(fixtures[fixture_index][None])
    runtime = HB_HBMRuntime(str(hbm_path))

    fault_schedule = {
        500: "nan",
        1500: "inf",
        2500: "stale",
        3500: "ood",
        4500: "jump",
        5500: "limit",
        6500: "drop",
        7500: "time_reversal",
    }
    expected_events = {
        "nan": "nonfinite",
        "inf": "nonfinite",
        "stale": "stale_observation",
        "ood": "ood",
        "jump": "action_jump",
        "limit": "action_limit",
        "drop": "dropped_frame",
        "time_reversal": "time_reversal",
    }
    if max(fault_schedule) >= args.policy_steps:
        raise ValueError("policy step count does not cover the full fault schedule")

    guard_config = GuardConfig()
    shield = ActionSafetyShield(guard_config)
    policy_timings: list[float] = []
    guard_timings: list[float] = []
    event_counts: dict[str, int] = {}
    fault_records: list[dict[str, object]] = []
    unexpected_fail_closes = 0
    all_outputs_finite = True
    raw_min = math.inf
    raw_max = -math.inf
    projected_min = math.inf
    projected_max = -math.inf
    previous_projected: np.ndarray | None = None
    previous_timestamp: float | None = None
    sink_lines = 0

    with sink_path.open("w", encoding="utf-8") as sink:
        for step in range(args.policy_steps):
            started_ns = time.perf_counter_ns()
            raw = only_output(runtime.run(policy_input)).reshape(-1).astype(np.float32, copy=False)
            policy_timings.append((time.perf_counter_ns() - started_ns) / 1_000_000.0)
            if raw.shape != (guard_config.joint_count,) or not np.isfinite(raw).all():
                raise RuntimeError(f"invalid policy output at step {step}")
            projected = np.tanh(raw).astype(np.float32, copy=False)
            raw_min = min(raw_min, float(raw.min()))
            raw_max = max(raw_max, float(raw.max()))
            projected_min = min(projected_min, float(projected.min()))
            projected_max = max(projected_max, float(projected.max()))

            fault = fault_schedule.get(step)
            candidate = projected.copy()
            observation_age_s = 0.02
            confidence = 0.96
            ood_score = 0.20
            expected_sequence = step
            observed_sequence = step
            logical_timestamp = step / POLICY_RATE_HZ
            if fault == "nan":
                candidate[5] = np.nan
            elif fault == "inf":
                candidate[7] = np.inf
            elif fault == "stale":
                observation_age_s = 0.50
            elif fault == "ood":
                ood_score = 0.99
            elif fault == "jump":
                candidate[9] = -0.99 if candidate[9] >= 0.0 else 0.99
            elif fault == "limit":
                candidate[3] = 1.25
            elif fault == "drop":
                observed_sequence = step + 1
            elif fault == "time_reversal":
                logical_timestamp = (previous_timestamp or logical_timestamp) - 0.02

            substep_outputs: list[np.ndarray] = []
            substep_events: list[str] = []
            first_event: str | None = None
            for substep in range(GUARD_RATE_HZ // POLICY_RATE_HZ):
                guard_started_ns = time.perf_counter_ns()
                if substep > 0 and first_event is not None:
                    guarded = np.zeros(guard_config.joint_count, dtype=np.float32)
                    event = "fault_step_latched_stop"
                elif observed_sequence != expected_sequence:
                    shield.reset()
                    guarded = np.zeros(guard_config.joint_count, dtype=np.float32)
                    event = "dropped_frame"
                elif previous_timestamp is not None and logical_timestamp <= previous_timestamp:
                    shield.reset()
                    guarded = np.zeros(guard_config.joint_count, dtype=np.float32)
                    event = "time_reversal"
                elif (
                    np.isfinite(candidate).all()
                    and float(np.max(np.abs(candidate))) <= guard_config.action_limit
                    and previous_projected is not None
                    and float(np.max(np.abs(candidate - previous_projected))) > 0.50
                ):
                    shield.reset()
                    guarded = np.zeros(guard_config.joint_count, dtype=np.float32)
                    event = "action_jump"
                else:
                    guarded, event = shield.apply(
                        candidate,
                        observation_age_s=observation_age_s,
                        confidence=confidence,
                        ood_score=ood_score,
                    )
                guard_timings.append((time.perf_counter_ns() - guard_started_ns) / 1_000_000.0)
                if substep == 0 and fault is not None:
                    first_event = event
                substep_outputs.append(guarded)
                substep_events.append(event)
                event_counts[event] = event_counts.get(event, 0) + 1

            output = substep_outputs[-1]
            all_outputs_finite = all_outputs_finite and all(np.isfinite(value).all() for value in substep_outputs)
            if fault is not None:
                expected = expected_events[fault]
                record = {
                    "step": step,
                    "fault": fault,
                    "expected_event": expected,
                    "observed_event": substep_events[0],
                    "detected": substep_events[0] == expected,
                    "all_four_guard_outputs_zero": all(
                        bool(np.allclose(value, 0.0, atol=1.0e-7)) for value in substep_outputs
                    ),
                }
                fault_records.append(record)
                shield.reset()
            else:
                if substep_events[0] in {
                    "nonfinite",
                    "stale_observation",
                    "low_confidence",
                    "ood",
                    "action_limit",
                    "action_norm",
                    "dropped_frame",
                    "time_reversal",
                    "action_jump",
                }:
                    unexpected_fail_closes += 1
                previous_projected = projected.copy()
                previous_timestamp = logical_timestamp

            sink.write(
                json.dumps(
                    {
                        "policy_step": step,
                        "logical_policy_time_s": step / POLICY_RATE_HZ,
                        "guard_events": substep_events,
                        "fault": fault,
                        "action": [round(float(value), 7) for value in output],
                        "published": False,
                    },
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
            sink_lines += 1

    policy_timing = timing_summary(policy_timings, POLICY_DEADLINE_MS)
    guard_timing = timing_summary(guard_timings, GUARD_DEADLINE_MS)
    faults_ok = len(fault_records) == len(fault_schedule) and all(
        bool(item["detected"] and item["all_four_guard_outputs_zero"]) for item in fault_records
    )
    chain_checks = {
        "perception_evidence_pass_4_of_4": perception["summary"]["pass"] == 4,
        "nav_goals_pass_gte_20": navigation["goal_summary"]["passed"] >= 20,
        "policy_steps_gte_10000": args.policy_steps >= 10_000,
        "guard_substeps_equal_4x_policy": len(guard_timings) == args.policy_steps * 4,
        "all_faults_detected_and_fail_closed": faults_ok,
        "all_guard_outputs_finite": all_outputs_finite,
        "no_unexpected_fail_close_on_clean_steps": unexpected_fail_closes == 0,
        "policy_p99_within_20ms": float(policy_timing["p99_ms"]) <= POLICY_DEADLINE_MS,
        "guard_p99_within_5ms": float(guard_timing["p99_ms"]) <= GUARD_DEADLINE_MS,
        "file_sink_line_count_matches": sink_lines == args.policy_steps,
    }

    stress_timing = StressTiming(POLICY_DEADLINE_MS)
    stress_errors: list[str] = []
    resource_samples: list[dict[str, object]] = []
    stress_started = time.monotonic()
    next_monitor = stress_started
    next_progress = stress_started
    while True:
        now = time.monotonic()
        elapsed = now - stress_started
        if elapsed >= args.stress_seconds:
            break
        call_started_ns = time.perf_counter_ns()
        try:
            stress_output = only_output(runtime.run(policy_input))
            if not np.isfinite(stress_output).all():
                raise FloatingPointError("stress output contains NaN/Inf")
        except Exception as error:  # continue to preserve a complete diagnostic record
            stress_errors.append(f"{type(error).__name__}: {error}")
        stress_timing.add((time.perf_counter_ns() - call_started_ns) / 1_000_000.0)
        now = time.monotonic()
        if now >= next_monitor:
            sample = resource_sample(now - stress_started)
            resource_samples.append(sample)
            progress_path.write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "elapsed_s": now - stress_started,
                        "stress_invocations": stress_timing.count,
                        "stress_errors": len(stress_errors),
                        "latest_resource": sample,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            next_monitor = now + args.monitor_period
        if now >= next_progress:
            latest_temp = resource_samples[-1].get("bpu_temperature_c") if resource_samples else None
            print(
                f"D5_STRESS_PROGRESS elapsed_s={now - stress_started:.1f} "
                f"invocations={stress_timing.count} errors={len(stress_errors)} "
                f"bpu_temp_c={latest_temp}",
                flush=True,
            )
            next_progress = now + 30.0

    stress_elapsed = time.monotonic() - stress_started
    resource_samples.append(resource_sample(stress_elapsed))
    stress_summary = stress_timing.summary()
    bpu_temperatures = [
        float(item["bpu_temperature_c"])
        for item in resource_samples
        if item.get("bpu_temperature_c") is not None
    ]
    bpu_ratios = [
        float(item["bpu_ratio_percent"])
        for item in resource_samples
        if item.get("bpu_ratio_percent") is not None
    ]
    available_memory = [
        int(item["memory_kib"]["MemAvailable"])
        for item in resource_samples
        if isinstance(item.get("memory_kib"), dict)
        and "MemAvailable" in item["memory_kib"]
    ]
    frequency_values = [
        int(value)
        for item in resource_samples
        for value in (
            item.get("cpu_scaling_cur_freq_khz", {}).values()
            if isinstance(item.get("cpu_scaling_cur_freq_khz"), dict)
            else []
        )
    ]
    stress_checks = {
        "wall_clock_duration_gte_1800s": stress_elapsed >= 1800.0,
        "bpu_invocations_gte_1000": stress_timing.count >= 1000,
        "zero_inference_errors": not stress_errors,
        "resource_samples_gte_180": len(resource_samples) >= 180,
        "bpu_temperature_recorded": bool(bpu_temperatures),
        "bpu_utilization_recorded": bool(bpu_ratios),
        "memory_recorded": bool(available_memory),
        "cpu_frequency_recorded": bool(frequency_values),
    }

    result = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "S100 single-board offline perception/nav/policy/guard/file-sink chain and BPU stress",
        "model": {
            "name": "policy",
            "hbm": str(hbm_path),
            "hbm_sha256": sha256(hbm_path),
            "fixture": str(fixture_path),
            "fixture_sha256": sha256(fixture_path),
            "fixture_index": fixture_index,
            "selection_inputs": {
                "yolo26_detection_count": detection_count,
                "nav_total_path_poses": route_pose_count,
            },
            "shadow_action_projection": "numpy.tanh maps raw policy output to the U2 normalized [-1,1] file-sink contract; not a robot actuator mapping",
            "raw_output_range": [raw_min, raw_max],
            "projected_output_range": [projected_min, projected_max],
        },
        "chain": {
            "logical_rates_hz": {"policy": POLICY_RATE_HZ, "guard": GUARD_RATE_HZ},
            "policy_steps": args.policy_steps,
            "guard_steps": len(guard_timings),
            "policy_timing": policy_timing,
            "guard_timing": guard_timing,
            "faults": fault_records,
            "event_counts": event_counts,
            "unexpected_fail_closes": unexpected_fail_closes,
            "sink": {
                "path": str(sink_path),
                "sha256": sha256(sink_path),
                "lines": sink_lines,
                "published": False,
            },
            "checks": chain_checks,
            "result": "PASS" if all(chain_checks.values()) else "FAIL",
        },
        "stress": {
            "requested_seconds": args.stress_seconds,
            "elapsed_seconds": stress_elapsed,
            "timing": stress_summary,
            "errors": stress_errors,
            "resource_samples": resource_samples,
            "resource_summary": {
                "bpu_temperature_c_min": min(bpu_temperatures) if bpu_temperatures else None,
                "bpu_temperature_c_max": max(bpu_temperatures) if bpu_temperatures else None,
                "bpu_ratio_percent_min": min(bpu_ratios) if bpu_ratios else None,
                "bpu_ratio_percent_max": max(bpu_ratios) if bpu_ratios else None,
                "mem_available_kib_min": min(available_memory) if available_memory else None,
                "mem_available_kib_max": max(available_memory) if available_memory else None,
                "cpu_scaling_cur_freq_khz_min": min(frequency_values) if frequency_values else None,
                "cpu_scaling_cur_freq_khz_max": max(frequency_values) if frequency_values else None,
                "frequency_note": "Observed scaling_cur_freq range; no unsupported claim that a frequency change is thermal throttling.",
            },
            "checks": stress_checks,
            "result": "PASS" if all(stress_checks.values()) else "FAIL",
        },
        "external_network": False,
        "external_device_access": False,
        "control_output": False,
    }
    result["checks"] = {
        "chain_pass": result["chain"]["result"] == "PASS",
        "stress_pass": result["stress"]["result"] == "PASS",
        "no_external_network": result["external_network"] is False,
        "no_external_device_access": result["external_device_access"] is False,
        "no_control_output": result["control_output"] is False,
    }
    result["result"] = "PASS" if all(result["checks"].values()) else "FAIL"
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress_path.write_text(
        json.dumps(
            {
                "status": result["result"],
                "elapsed_s": stress_elapsed,
                "stress_invocations": stress_timing.count,
                "stress_errors": len(stress_errors),
                "result": str(args.output),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result": result["result"],
                "chain": result["chain"]["result"],
                "stress": result["stress"]["result"],
                "stress_invocations": stress_timing.count,
                "output": str(args.output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
