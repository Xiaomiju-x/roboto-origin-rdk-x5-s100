#!/usr/bin/env bash

set -euo pipefail

ROOT=/home/sunrise/workspaces/new_project/roboto_origin_s100
source "$ROOT/scripts/env_s100.sh" >/dev/null

test "$ROS_DOMAIN_ID" = 42
test "$ROOT" = "$ROBOTO_S100_ROOT"
test -d "$ROOT"
test "$(id -un)" = sunrise

if find "$ROOT" -xdev -type l -lname '/dev/*' -print -quit | grep -q .; then
  printf 'ERROR: project contains a symlink into /dev\n' >&2
  exit 1
fi

if ip -details link show type can 2>/dev/null | grep -Eq '^[0-9]+: .*state UP'; then
  printf 'ERROR: an active Linux CAN interface was detected\n' >&2
  exit 1
fi

for service in ssh.service x11vnc.service; do
  state="$(systemctl is-active "$service" 2>/dev/null || true)"
  if [ "$state" != active ]; then
    printf 'ERROR: required remote access service is not active: %s=%s\n' "$service" "$state" >&2
    exit 1
  fi
done

if pgrep -af '(^|/)(depth_node|inference_node|robots_localization_node|realsense2_camera_node|canhal|can2ipc)( |$)' >/dev/null; then
  printf 'ERROR: a hardware/control-capable project process is already running\n' >&2
  exit 1
fi

listeners="$(ss -ltnH | awk '$4 ~ /:(91[0-9][0-9])$/ {print $4}' | paste -sd, -)"
printf 'SAFETY_PASS domain=%s listeners=%s ssh=active vnc=active device_access=forbidden\n' \
  "$ROS_DOMAIN_ID" "${listeners:-none}"
