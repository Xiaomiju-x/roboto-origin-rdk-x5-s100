#!/usr/bin/env bash

set -eo pipefail

project_root="/home/sunrise/workspaces/new_project/roboto_origin"
deps_root="$project_root/deps"
phase="${1:-all}"
parallel_jobs="${ROBOTO_BUILD_JOBS:-2}"

source "$project_root/scripts/env_x5_deps.sh" >/dev/null
bash "$project_root/scripts/verify_phase_a_env.sh"

case "$parallel_jobs" in
  ''|*[!0-9]*|0)
    printf 'ERROR: ROBOTO_BUILD_JOBS must be a positive integer\n' >&2
    exit 2
    ;;
esac

# colcon-cmake otherwise appends -j/-l using every online CPU. Supplying
# MAKEFLAGS makes colcon preserve this project-level limit.
export MAKEFLAGS="-j$parallel_jobs -l$parallel_jobs"
export CMAKE_BUILD_PARALLEL_LEVEL="$parallel_jobs"

mkdir -p \
  "$deps_root/src" \
  "$deps_root/build" \
  "$deps_root/install" \
  "$deps_root/logs"

run_phase() {
  local requested="$1"
  [ "$phase" = "all" ] || [ "$phase" = "$requested" ]
}

assert_clean_checkout() {
  local repository="$1"
  local dirty

  dirty="$(git -C "$repository" status --porcelain --ignore-submodules=none)"
  if [ -n "$dirty" ]; then
    printf 'ERROR: official checkout is not clean: %s\n%s\n' "$repository" "$dirty" >&2
    return 1
  fi

  git -C "$repository" submodule foreach --quiet --recursive \
    'test -z "$(git status --porcelain)"' >/dev/null
}

prepare_stage() {
  local official_dir="$1"
  local stage_dir="$2"
  local patch_file="$3"

  assert_clean_checkout "$official_dir"

  if [ ! -d "$stage_dir" ]; then
    cp -a -- "$official_dir" "$stage_dir"
  fi

  if git -C "$stage_dir" apply --check "$patch_file" 2>/dev/null; then
    git -C "$stage_dir" apply "$patch_file"
  elif git -C "$stage_dir" apply --reverse --check "$patch_file" 2>/dev/null; then
    printf 'Audited patch already applied: %s\n' "$patch_file"
  else
    printf 'ERROR: staged source does not match audited patch: %s\n' "$patch_file" >&2
    return 1
  fi
}

apply_audited_patch() {
  local stage_dir="$1"
  local patch_file="$2"

  if git -C "$stage_dir" apply --check "$patch_file" 2>/dev/null; then
    git -C "$stage_dir" apply "$patch_file"
  elif git -C "$stage_dir" apply --reverse --check "$patch_file" 2>/dev/null; then
    printf 'Audited patch already applied: %s\n' "$patch_file"
  else
    printf 'ERROR: staged source does not match audited patch: %s\n' "$patch_file" >&2
    return 1
  fi
}

build_navigation() {
  local official_dir="$project_root/src/official/roboparty_navigation"
  local stage_dir="$deps_root/src/roboparty_navigation-x5"
  local patch_file="$project_root/patches/roboparty-navigation-x5-build.patch"
  local runtime_patch_file="$project_root/patches/roboparty-navigation-x5-runtime.patch"
  local thread_patch_file="$project_root/patches/roboparty-navigation-x5-thread-lifecycle.patch"
  local ros_entity_patch_file="$project_root/patches/roboparty-navigation-x5-ros-entity-lifecycle.patch"
  local common_globals_patch_file="$project_root/patches/roboparty-navigation-x5-common-globals.patch"

  prepare_stage "$official_dir" "$stage_dir" "$patch_file"
  apply_audited_patch "$stage_dir" "$runtime_patch_file"
  if grep -Fq 'main_process_thread_ = std::thread' \
      "$stage_dir/robots_localization_ros2/src/robots_localization_node.h" \
      && grep -Fq 'node.reset();' \
      "$stage_dir/robots_localization_ros2/src/robots_localization_node.cpp" \
      && ! grep -Fq 'signal(SIGINT, SigHandle);' \
      "$stage_dir/robots_localization_ros2/src/robots_localization_node.h" \
      && ! grep -Fq 'mainThread.detach();' \
      "$stage_dir/robots_localization_ros2/src/robots_localization_node.h"; then
    printf 'Audited thread lifecycle patch markers already present: %s\n' "$thread_patch_file"
  else
    apply_audited_patch "$stage_dir" "$thread_patch_file"
  fi
  apply_audited_patch "$stage_dir" "$ros_entity_patch_file"
  apply_audited_patch "$stage_dir" "$common_globals_patch_file"

  colcon --log-base "$deps_root/logs/official_navigation_x5" build \
    --base-paths "$stage_dir" \
    --packages-select \
      nlink_message serial nlink_parser_ros2 \
      nav2_localization_adapter robots_localization \
    --build-base "$deps_root/build/official_navigation_x5" \
    --install-base "$deps_root/install/official_navigation" \
    --executor sequential \
    --parallel-workers 1 \
    --event-handlers console_cohesion+ \
    --cmake-args \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -DPYTHON_EXECUTABLE=/usr/bin/python3 \
      -DPython3_EXECUTABLE=/usr/bin/python3 \
      -DSophus_DIR="$deps_root/install/share/sophus/cmake"
}

build_deploy() {
  local official_dir="$project_root/src/official/roboparty_deploy"
  local stage_dir="$deps_root/src/roboparty_deploy-x5"
  local patch_file="$project_root/patches/roboparty-deploy-x5-build.patch"

  prepare_stage "$official_dir" "$stage_dir" "$patch_file"

  colcon --log-base "$deps_root/logs/official_deploy_x5" build \
    --base-paths "$stage_dir/src" \
    --packages-select \
      camera roboparty_imu roboparty_motors roboparty_inference \
    --build-base "$deps_root/build/official_deploy_x5" \
    --install-base "$deps_root/install/official_deploy" \
    --executor sequential \
    --parallel-workers 1 \
    --event-handlers console_cohesion+ \
    --cmake-args \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -DPython3_EXECUTABLE=/usr/bin/python3
}

case "$phase" in
  all|navigation|deploy) ;;
  *)
    printf 'Usage: %s [all|navigation|deploy]\n' "$0" >&2
    exit 2
    ;;
esac

run_phase navigation && build_navigation
run_phase deploy && build_deploy

printf 'Official X5 build phase complete: %s (jobs=%s)\n' "$phase" "$parallel_jobs"
