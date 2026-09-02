#!/usr/bin/env bash

set -eo pipefail

input_mode="${1:-zero}"
case "$input_mode" in
  zero|deterministic) ;;
  *)
    printf 'Usage: %s [zero|deterministic]\n' "$0" >&2
    exit 2
    ;;
esac

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
deps_root="$project_root/deps"
source_file="$project_root/probes/onnx_runtime_probe.cpp"
probe_dir="$deps_root/probes"
probe_binary="$probe_dir/onnx_runtime_probe"
include_dir="$deps_root/src/roboparty_deploy-x5/src/inference/thirdparty/onnxruntime/include"
runtime_dir="$deps_root/install/official_deploy/roboparty_inference/lib"
policy_dir="$deps_root/install/official_deploy/roboparty_inference/share/roboparty_inference/robots/rpo/models"
encoder_path="$deps_root/install/official_deploy/camera/share/camera/models/encoder.onnx"
evidence_dir="$project_root/evidence/x5/onnx_runtime"
stamp="$(date +%Y%m%dT%H%M%S%z)"
evidence_file="$evidence_dir/onnx_synthetic_${input_mode}_$stamp.json"

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
bash "$project_root/scripts/verify_phase_a_env.sh"

assert_safety_state() {
  if ! ip link show can0 2>/dev/null | grep -q 'state DOWN'; then
    printf 'ERROR: can0 is absent or not DOWN; refusing synthetic probe\n' >&2
    return 1
  fi

  local service state
  for service in \
    embodied_brain.service cockpit_bridge.service \
    eb_sensor_watchdog.service xrd-v6-8890.service; do
    state="$(systemctl is-active "$service" 2>/dev/null || true)"
    if [ "$state" != "inactive" ]; then
      printf 'ERROR: frozen service is not inactive: %s=%s\n' "$service" "$state" >&2
      return 1
    fi
  done

  if pgrep -f '(^|/)(depth_node|inference_node|robots_localization_node|realsense2_camera_node)( |$)' >/dev/null; then
    printf 'ERROR: official runtime node process detected; refusing synthetic probe\n' >&2
    return 1
  fi
}

assert_safety_state
test "$ROS_DOMAIN_ID" = "42"
test -f "$source_file"
test -f "$include_dir/onnxruntime_cxx_api.h"
test -f "$runtime_dir/libonnxruntime.so"
mkdir -p "$probe_dir" "$evidence_dir"

if [ ! -x "$probe_binary" ] || [ "$source_file" -nt "$probe_binary" ]; then
  g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic \
    -I"$include_dir" \
    "$source_file" \
    -L"$runtime_dir" \
    -Wl,-rpath,"$runtime_dir" \
    -lonnxruntime -pthread \
    -o "$probe_binary"
fi

models=(
  "policy|23|$policy_dir/policy.onnx"
  "policy_amp|23|$policy_dir/policy_amp.onnx"
  "policy_attn_enc|23|$policy_dir/policy_attn_enc.onnx"
  "policy_dance0|23|$policy_dir/policy_dance0.onnx"
  "policy_dance1|23|$policy_dir/policy_dance1.onnx"
  "policy_getup|23|$policy_dir/policy_getup.onnx"
  "policy_interrupt|23|$policy_dir/policy_interrupt.onnx"
  "policy_parkour|23|$policy_dir/policy_parkour.onnx"
  "policy_wave|23|$policy_dir/policy_wave.onnx"
  "depth_encoder|128|$encoder_path"
)

temporary_file="$evidence_file.tmp"
set +e
if [ "$input_mode" = "deterministic" ]; then
  ROBOTO_PROBE_INPUT_MODE=deterministic \
    "$probe_binary" "${models[@]}" > "$temporary_file"
else
  "$probe_binary" "${models[@]}" > "$temporary_file"
fi
probe_status=$?
set -e
mv -- "$temporary_file" "$evidence_file"

/usr/bin/python3 - "$evidence_file" "$input_mode" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
requested_mode = sys.argv[2]
report = json.loads(path.read_text(encoding="utf-8"))
models = report.get("models", [])
expected_mode = {
    "zero": "synthetic_zero_float32",
    "deterministic": "synthetic_deterministic_trigonometric_float32",
}[requested_mode]
assert report.get("input_mode") == expected_mode
assert report.get("device_access") is False
assert len(models) == 10
assert {model["label"] for model in models} == {
    "policy", "policy_amp", "policy_attn_enc", "policy_dance0",
    "policy_dance1", "policy_getup", "policy_interrupt", "policy_parkour",
    "policy_wave", "depth_encoder",
}
if report.get("result") != "PASS":
    failed = [model["label"] for model in models if model.get("status") != "PASS"]
    raise SystemExit("synthetic inference failed: " + ", ".join(failed))
for model in models:
    assert model["output_elements"] == model["expected_output_elements"]
    assert model["finite_elements"] == model["output_elements"]
    assert len(model["output_values"]) == model["output_elements"]
print(f"validated_models={len(models)} result={report['result']}")
PY

assert_safety_state
sha256sum "$evidence_file"
printf 'evidence_file=%s\n' "$evidence_file"
exit "$probe_status"
