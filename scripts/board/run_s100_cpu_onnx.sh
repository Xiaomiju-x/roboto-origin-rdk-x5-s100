#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null
bash "$ROOT/scripts/verify_s100_safety.sh"

STAMP="$(date --iso-8601=seconds | tr ':+' '--')"
OUT="$ROOT/evidence/d2_cpu_onnx/$STAMP"
ARCHIVE="$ROOT/third_party/archives/onnxruntime-linux-aarch64-1.21.0.tgz"
RUNTIME_ROOT="$ROOT/third_party/runtime"
ORT="$RUNTIME_ROOT/onnxruntime-linux-aarch64-1.21.0"
PROBE="$ROOT/build/onnx_runtime_probe"
mkdir -p "$OUT" "$ROOT/build" "$RUNTIME_ROOT"

cd "$ROOT"
sha256sum -c inventory/s100_model_sha256.txt | tee "$OUT/model_hashes.txt"
printf '%s  %s\n' \
  4508084bde1232ee1ab4b6fad2155be0ea2ccab1c1aae9910ddb3fb68a60805e \
  third_party/archives/onnxruntime-linux-aarch64-1.21.0.tgz \
  | sha256sum -c - | tee "$OUT/runtime_hash.txt"

if [ ! -f "$ORT/include/onnxruntime_cxx_api.h" ]; then
  tar -xzf "$ARCHIVE" -C "$RUNTIME_ROOT"
fi

g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic \
  -I"$ORT/include" "$ROOT/probes/onnx_runtime_probe.cpp" \
  -L"$ORT/lib" -Wl,-rpath,"$ORT/lib" -lonnxruntime -pthread \
  -o "$PROBE"

models=(
  "policy|23|$ROOT/models/official/rpo/policy.onnx"
  "policy_amp|23|$ROOT/models/official/rpo/policy_amp.onnx"
  "policy_attn_enc|23|$ROOT/models/official/rpo/policy_attn_enc.onnx"
  "policy_dance0|23|$ROOT/models/official/rpo/policy_dance0.onnx"
  "policy_dance1|23|$ROOT/models/official/rpo/policy_dance1.onnx"
  "policy_getup|23|$ROOT/models/official/rpo/policy_getup.onnx"
  "policy_interrupt|23|$ROOT/models/official/rpo/policy_interrupt.onnx"
  "policy_parkour|23|$ROOT/models/official/rpo/policy_parkour.onnx"
  "policy_wave|23|$ROOT/models/official/rpo/policy_wave.onnx"
  "depth_encoder|128|$ROOT/models/official/depth/encoder.onnx"
)

cat /proc/loadavg > "$OUT/load_before.txt"
ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -20 > "$OUT/processes_before.txt"
if [ -x /usr/bin/time ]; then
  ROBOTO_PROBE_INPUT_MODE=deterministic \
  ROBOTO_PROBE_WARMUP_RUNS=20 \
  ROBOTO_PROBE_MEASURED_RUNS=100 \
    /usr/bin/time -v -o "$OUT/resource.txt" \
    "$PROBE" "${models[@]}" > "$OUT/runtime.json"
else
  printf 'external /usr/bin/time unavailable; functional and latency evidence retained\n' \
    > "$OUT/resource.txt"
  ROBOTO_PROBE_INPUT_MODE=deterministic \
  ROBOTO_PROBE_WARMUP_RUNS=20 \
  ROBOTO_PROBE_MEASURED_RUNS=100 \
    "$PROBE" "${models[@]}" > "$OUT/runtime.json"
fi
cat /proc/loadavg > "$OUT/load_after.txt"

python3 - \
  "$OUT/runtime.json" \
  "$ROOT/data/fixtures/x5_onnx_deterministic_reference.json" \
  "$OUT/summary.json" \
  "$OUT/load_before.txt" <<'PY'
import json
import math
import os
import pathlib
import statistics
import sys

runtime_path, reference_path, output_path, load_path = map(pathlib.Path, sys.argv[1:])
runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
reference = json.loads(reference_path.read_text(encoding="utf-8"))
reference_by_label = {item["label"]: item for item in reference["models"]}

def percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]

comparisons = []
for model in runtime["models"]:
    label = model["label"]
    expected = reference_by_label[label]["output_values"]
    actual = model["output_values"]
    dot = sum(a * b for a, b in zip(actual, expected))
    norm_a = math.sqrt(sum(a * a for a in actual))
    norm_b = math.sqrt(sum(b * b for b in expected))
    cosine = dot / (norm_a * norm_b) if norm_a and norm_b else (1.0 if actual == expected else 0.0)
    maximum = max(abs(a - b) for a, b in zip(actual, expected))
    latencies = model["elapsed_ms"]
    comparisons.append({
        "label": label,
        "status": model["status"],
        "max_abs_error_vs_x5": maximum,
        "cosine_similarity_vs_x5": cosine,
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
        "mean_ms": statistics.fmean(latencies),
    })

load1 = float(load_path.read_text(encoding="utf-8").split()[0])
cpu_count = os.cpu_count() or 1
idle_gate = load1 / cpu_count <= 0.75
functional = (
    runtime.get("result") == "PASS"
    and runtime.get("device_access") is False
    and runtime.get("warmup_runs") >= 20
    and runtime.get("measured_runs") >= 100
    and all(item["max_abs_error_vs_x5"] <= 2e-5 for item in comparisons)
    and all(item["cosine_similarity_vs_x5"] >= 0.999999 for item in comparisons)
)
policy_p99 = max(item["p99_ms"] for item in comparisons if item["label"] != "depth_encoder")
payload = {
    "schema_version": 1,
    "result": "PASS" if functional else "FAIL",
    "scope": "S100 CPU reproduction of nine official policies and depth encoder",
    "device_access": False,
    "control_output": False,
    "input_fixture": runtime.get("input_mode"),
    "x5_reference_sha256": __import__("hashlib").sha256(reference_path.read_bytes()).hexdigest(),
    "load1_before": load1,
    "cpu_count": cpu_count,
    "load1_per_cpu": load1 / cpu_count,
    "benchmark_idle_gate": "PASS" if idle_gate else "DEFERRED_BUSY_SYSTEM",
    "performance_observed_only": not idle_gate,
    "policy_p99_ms_max": policy_p99,
    "policy_p99_target_ms": 20.0,
    "policy_p99_target_observed": policy_p99 <= 20.0,
    "models": comparisons,
}
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if functional else 1)
PY

bash "$ROOT/scripts/verify_s100_safety.sh" | tee "$OUT/safety_after.txt"
if pgrep -af "$PROBE" > "$OUT/residuals.txt"; then
  printf 'ERROR: ONNX probe process remained after exit\n' >&2
  exit 1
fi
printf '%s\n' "$OUT"
