#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
source "$project_root/scripts/env_x5.sh"

failures=0

pass() {
  printf 'PASS  %s\n' "$1"
}

fail() {
  printf 'FAIL  %s\n' "$1" >&2
  failures=$((failures + 1))
}

if [ "$ROS_DOMAIN_ID" = "42" ]; then
  pass "ROS_DOMAIN_ID=42"
else
  fail "ROS_DOMAIN_ID is not 42"
fi

if command -v ros2 >/dev/null 2>&1; then
  pass "ROS 2 CLI available: $(command -v ros2)"
else
  fail "ROS 2 CLI unavailable after environment setup"
fi

declare -A pins=(
  [roboparty_deploy]="a8a0f1557cc5d085234b8bac248a8f543342f531"
  [roboparty_train]="92008a6317d6d0efe8b58abc2fb3c630c75992c0"
  [roboparty_navigation]="d6ab9913599672af78422eb2e89a16c0460a8009"
  [rpo_description]="37aac9ca665e92731444a1618320078e7ba21569"
  [GMR]="f7bfaead0d0b896e6b74a64bc6948ca8b09d420b"
)

for repo in roboparty_deploy roboparty_train roboparty_navigation rpo_description GMR; do
  repo_path="$project_root/src/official/$repo"
  if [ ! -d "$repo_path/.git" ]; then
    fail "official checkout missing: $repo"
    continue
  fi

  actual="$(git -C "$repo_path" rev-parse HEAD 2>/dev/null || true)"
  if [ "$actual" = "${pins[$repo]}" ]; then
    pass "official pin: $repo"
  else
    fail "official pin mismatch: $repo expected=${pins[$repo]} actual=$actual"
  fi

  if [ -z "$(git -C "$repo_path" status --short --untracked-files=no)" ]; then
    pass "tracked worktree clean: $repo"
  else
    fail "tracked worktree changed: $repo"
  fi

  if git -C "$repo_path" submodule status --recursive 2>/dev/null | grep -q '^-'; then
    fail "submodule not initialized: $repo"
  else
    pass "submodules initialized: $repo"
  fi
done

for service in embodied_brain.service cockpit_bridge.service eb_sensor_watchdog.service xrd-v6-8890.service; do
  if [ "$(systemctl is-active "$service" 2>/dev/null || true)" = "inactive" ]; then
    pass "frozen service inactive: $service"
  else
    fail "frozen service unexpectedly active: $service"
  fi
done

if ip link show can0 2>/dev/null | grep -q 'state DOWN'; then
  pass "can0 remains DOWN"
else
  fail "can0 is absent or not DOWN"
fi

if [ -r /sys/class/drm/card0-HDMI-A-1/status ] && \
   [ "$(cat /sys/class/drm/card0-HDMI-A-1/status)" = "connected" ]; then
  pass "HDMI connector reports connected"
else
  fail "HDMI connector is not connected"
fi

occupied="$(ss -lnt 2>/dev/null | grep -E ':91[0-9][0-9]([^0-9]|$)' || true)"
if [ -n "$occupied" ]; then
  printf 'INFO  occupied ports in 9100-9199 (do not reuse):\n%s\n' "$occupied"
else
  printf 'INFO  no occupied TCP ports found in 9100-9199\n'
fi

if [ "$failures" -eq 0 ]; then
  printf 'RESULT PASS (environment integrity only; no hardware or algorithm validation implied)\n'
  exit 0
fi

printf 'RESULT FAIL count=%s\n' "$failures" >&2
exit 1
