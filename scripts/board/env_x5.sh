#!/usr/bin/env bash

set -e

export ROBOTO_ORIGIN_ROOT="/home/sunrise/workspaces/new_project/roboto_origin"

source /home/sunrise/workspaces/new_project/env.sh

if [ -f /opt/ros/humble/setup.bash ]; then
  source /opt/ros/humble/setup.bash
fi

if [ -f /opt/tros/humble/setup.bash ]; then
  source /opt/tros/humble/setup.bash
fi

export ROS_DOMAIN_ID=42
export ROBOTO_ORIGIN_PORT_MIN=9104
export ROBOTO_ORIGIN_PORT_MAX=9199

case "$ROBOTO_ORIGIN_ROOT" in
  /home/sunrise/workspaces/new_project/*) ;;
  *)
    printf 'ERROR: unsafe project root: %s\n' "$ROBOTO_ORIGIN_ROOT" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

if [ "$ROS_DOMAIN_ID" != "42" ]; then
  printf 'ERROR: ROS_DOMAIN_ID must be 42, got %s\n' "$ROS_DOMAIN_ID" >&2
  return 1 2>/dev/null || exit 1
fi

printf 'Roboto X5 environment active: root=%s ROS_DOMAIN_ID=%s ports=%s-%s\n' \
  "$ROBOTO_ORIGIN_ROOT" "$ROS_DOMAIN_ID" \
  "$ROBOTO_ORIGIN_PORT_MIN" "$ROBOTO_ORIGIN_PORT_MAX"
