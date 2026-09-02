#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
evidence_dir="$project_root/evidence/x5/prework"
stamp="$(date +%Y%m%dT%H%M%S%z)"
output_file="$evidence_dir/offline_prework_$stamp.txt"
failures=0

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
mkdir -p "$evidence_dir"

onnx_report="$(find "$project_root/evidence/x5/onnx_runtime" -maxdepth 1 -type f -name 'onnx_synthetic_*.json' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
navigation_report="$(find "$project_root/evidence/x5/navigation_offline" -mindepth 2 -maxdepth 2 -type f -name 'pcd_to_pgm_summary.json' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
navigation_dir="$(dirname "$navigation_report")"
static_report="$navigation_dir/navigation_static_audit.json"

{
  printf 'captured_at=%s\n' "$(date --iso-8601=seconds)"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'addresses=%s\n' "$(hostname -I | xargs)"
  printf 'machine=%s\n' "$(uname -m)"
  printf 'kernel=%s\n' "$(uname -r)"
  printf 'ROS_DOMAIN_ID=%s\n' "$ROS_DOMAIN_ID"

  printf '\n[Offline algorithm reports]\n'
  if /usr/bin/python3 - "$onnx_report" "$navigation_report" "$static_report" <<'PY'
import json
import pathlib
import sys

onnx = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
navigation = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
static = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))
assert onnx["result"] == "PASS" and len(onnx["models"]) == 10
assert all(model["status"] == "PASS" for model in onnx["models"])
assert navigation["result"] == "PASS"
assert static["result"] == "PASS"
assert not onnx["device_access"]
assert not navigation["device_access"] and not navigation["ros_nodes_started"]
assert not static["device_access"] and not static["ros_nodes_started"]
print("PASS\tONNX synthetic runtime: 10/10 models")
print(
    "PASS\tPCD-to-PGM: "
    f"{navigation['input']['filtered_point_count']} points -> "
    f"{navigation['output']['width']}x{navigation['output']['height']} grid"
)
print(
    "PASS\tNavigation static audit: "
    f"{static['counts']['launch_files']} launch, "
    f"{static['counts']['yaml_files']} YAML, "
    f"{static['counts']['warnings']} warning(s)"
)
PY
  then
    printf 'PASS\toffline report schemas and safety flags\n'
  else
    printf 'FAIL\toffline report validation\n'
    failures=$((failures + 1))
  fi

  printf '\n[Evidence hashes]\n'
  sha256sum "$onnx_report"
  find "$navigation_dir" -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum

  printf '\n[Project Python runtime]\n'
  if env -u PYTHONPATH "$project_root/deps/venv/open3d/bin/python" -c \
    'import PIL, scipy, open3d; print("PASS\tOpen3D=" + open3d.__version__ + " Pillow=" + PIL.__version__ + " SciPy=" + scipy.__version__)'; then
    :
  else
    printf 'FAIL\tproject Python runtime import\n'
    failures=$((failures + 1))
  fi

  printf '\n[Official checkout integrity]\n'
  for repository in \
    "$project_root/src/official/roboparty_deploy" \
    "$project_root/src/official/roboparty_train" \
    "$project_root/src/official/roboparty_navigation" \
    "$project_root/src/official/rpo_description" \
    "$project_root/src/official/GMR"; do
    printf 'repository=%s commit=%s\n' "$repository" "$(git -C "$repository" rev-parse HEAD)"
    dirty="$(git -C "$repository" status --porcelain --ignore-submodules=none)"
    if [ -z "$dirty" ]; then
      printf 'PASS\tclean official checkout\n'
    else
      printf 'FAIL\tdirty official checkout\n%s\n' "$dirty"
      failures=$((failures + 1))
    fi
  done

  printf '\n[Safety state]\n'
  ip -brief link show can0 2>&1 || true
  if ip link show can0 2>/dev/null | grep -q 'state DOWN'; then
    printf 'PASS\tcan0 remains DOWN\n'
  else
    printf 'FAIL\tcan0 absent or not DOWN\n'
    failures=$((failures + 1))
  fi

  for service in embodied_brain.service cockpit_bridge.service eb_sensor_watchdog.service xrd-v6-8890.service; do
    state="$(systemctl is-active "$service" 2>/dev/null || true)"
    printf '%s\t%s\n' "$service" "$state"
    if [ "$state" != "inactive" ]; then
      failures=$((failures + 1))
    fi
  done

  if pgrep -f '(^|/)(depth_node|inference_node|robots_localization_node|realsense2_camera_node)( |$)' >/dev/null; then
    printf 'FAIL\tofficial runtime node process detected\n'
    pgrep -af '(^|/)(depth_node|inference_node|robots_localization_node|realsense2_camera_node)( |$)' || true
    failures=$((failures + 1))
  else
    printf 'PASS\tno official runtime node process detected\n'
  fi

  printf '\n[Result]\n'
  if [ "$failures" -eq 0 ]; then
    printf 'RESULT PASS (X5 offline/no-device prework only; no sensor, actuator, closed-loop or robot validation implied)\n'
  else
    printf 'RESULT FAIL count=%s\n' "$failures"
  fi
} > "$output_file"

sha256sum "$output_file"
printf 'evidence_file=%s\n' "$output_file"
test "$failures" -eq 0
