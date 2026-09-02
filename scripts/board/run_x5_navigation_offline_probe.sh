#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
deps_root="$project_root/deps"
official_navigation="$project_root/src/official/roboparty_navigation"
converter_script="$official_navigation/nav2_localization_adapter/scripts/pcd2pgm.py"
input_pcd="$deps_root/install/official_navigation/robots_localization/share/robots_localization/PCD/map_ikdtree.pcd"
install_root="$deps_root/install/official_navigation"
stamp="$(date +%Y%m%dT%H%M%S%z)"
run_dir="$project_root/evidence/x5/navigation_offline/$stamp"
conversion_summary="$run_dir/pcd_to_pgm_summary.json"
static_summary="$run_dir/navigation_static_audit.json"
conversion_log="$run_dir/pcd_to_pgm.log"

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
bash "$project_root/scripts/verify_phase_a_env.sh"

assert_safety_state() {
  if ! ip link show can0 2>/dev/null | grep -q 'state DOWN'; then
    printf 'ERROR: can0 is absent or not DOWN\n' >&2
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
    printf 'ERROR: official runtime node process detected\n' >&2
    return 1
  fi
}

assert_official_clean() {
  local repository dirty
  for repository in \
    "$project_root/src/official/roboparty_deploy" \
    "$project_root/src/official/roboparty_navigation"; do
    dirty="$(git -C "$repository" status --porcelain --ignore-submodules=none)"
    if [ -n "$dirty" ]; then
      printf 'ERROR: official checkout is not clean: %s\n%s\n' "$repository" "$dirty" >&2
      return 1
    fi
  done
}

assert_safety_state
assert_official_clean
test "$ROS_DOMAIN_ID" = "42"
test -x "$ROBOTO_OPEN3D_PYTHON"
test -f "$converter_script"
test -f "$input_pcd"
mkdir -p "$run_dir"

set +e
env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 "$ROBOTO_OPEN3D_PYTHON" \
  "$project_root/probes/pcd_to_pgm_offline.py" \
  --official-script "$converter_script" \
  --input-pcd "$input_pcd" \
  --output-dir "$run_dir" \
  --summary "$conversion_summary" \
  > "$conversion_log" 2>&1
conversion_status=$?
set -e

static_status=1
if [ "$conversion_status" -eq 0 ]; then
  set +e
  /usr/bin/python3 "$project_root/probes/navigation_static_audit.py" \
    --install-root "$install_root" \
    --output "$static_summary"
  static_status=$?
  set -e
else
  tail -n 120 "$conversion_log" >&2
fi

assert_safety_state
assert_official_clean

if [ "$conversion_status" -ne 0 ] || [ "$static_status" -ne 0 ]; then
  printf 'RESULT FAIL conversion=%s static=%s\n' "$conversion_status" "$static_status" >&2
  exit 1
fi

/usr/bin/python3 - "$conversion_summary" "$static_summary" <<'PY'
import json
import pathlib
import sys

conversion = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
static = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert conversion["result"] == "PASS"
assert conversion["device_access"] is False
assert conversion["ros_nodes_started"] is False
assert static["result"] == "PASS"
assert static["device_access"] is False
assert static["ros_nodes_started"] is False
print(
    "validated_navigation_offline=PASS "
    f"grid={conversion['output']['width']}x{conversion['output']['height']} "
    f"static_warnings={static['counts']['warnings']}"
)
PY

find "$run_dir" -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum
printf 'evidence_dir=%s\n' "$run_dir"
