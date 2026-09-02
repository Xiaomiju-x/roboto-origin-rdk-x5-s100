#!/usr/bin/env bash

export ROBOTO_S100_ROOT="/home/sunrise/workspaces/new_project/roboto_origin_s100"

roboto_s100_restore_nounset=false
case "$-" in
  *u*)
    roboto_s100_restore_nounset=true
    set +u
    ;;
esac
source /home/sunrise/workspaces/new_project/env.sh
source /opt/ros/humble/setup.bash
if [ "$roboto_s100_restore_nounset" = true ]; then
  set -u
fi
unset roboto_s100_restore_nounset

export ROS_DOMAIN_ID=42
export ROBOTO_S100_PORT_MIN=9140
export ROBOTO_S100_PORT_MAX=9199

case "$ROBOTO_S100_ROOT" in
  /home/sunrise/workspaces/new_project/roboto_origin_s100) ;;
  *)
    printf 'ERROR: unsafe S100 project root: %s\n' "$ROBOTO_S100_ROOT" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

if [ "$ROS_DOMAIN_ID" != "42" ]; then
  printf 'ERROR: ROS_DOMAIN_ID must be 42, got %s\n' "$ROS_DOMAIN_ID" >&2
  return 1 2>/dev/null || exit 1
fi

if [ -f "$ROBOTO_S100_ROOT/install/setup.bash" ]; then
  source "$ROBOTO_S100_ROOT/install/setup.bash"
fi

if [ -d "$ROBOTO_S100_ROOT/third_party/python" ]; then
  export PYTHONPATH="$ROBOTO_S100_ROOT/third_party/python${PYTHONPATH:+:$PYTHONPATH}"
fi

printf 'Roboto S100 environment active: root=%s ROS_DOMAIN_ID=%s ports=%s-%s\n' \
  "$ROBOTO_S100_ROOT" "$ROS_DOMAIN_ID" \
  "$ROBOTO_S100_PORT_MIN" "$ROBOTO_S100_PORT_MAX"
