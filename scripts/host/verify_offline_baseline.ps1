[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$failures = [System.Collections.Generic.List[string]]::new()

function Test-Condition {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Label
    )

    if ($Condition) {
        Write-Host "PASS  $Label"
    }
    else {
        Write-Host "FAIL  $Label"
        $failures.Add($Label)
    }
}

$requiredFiles = @(
    'README.md',
    'docs\SCOPE_AND_BOUNDARIES.md',
    'docs\OFFICIAL_BASELINE.md',
    'docs\ARCHITECTURE.md',
    'docs\REPRODUCTION_PLAN.md',
    'docs\X5_POWER_ON_GATE.md',
    'docs\S100_MIGRATION_PLAN.md',
    'docs\XRD_REFERENCE_MAP.md',
    'docs\B0_MODEL_CONTRACT.md',
    'docs\B0_STATIC_READINESS.md',
    'docs\RPO_X5_JOINT_CONTRACT_CANDIDATE.md',
    'docs\X5_DEPENDENCY_PLAN.md',
    'docs\X5_DEPENDENCY_BUILD.md',
    'docs\X5_OFFLINE_PREWORK.md',
    'docs\OFFICIAL_OFFLINE_ACCEPTANCE.md',
    'docs\X5_FAST_LIO_RUNTIME_PATCH.md',
    'inventory\source_manifest.yaml',
    'inventory\host_capabilities.yaml',
    'inventory\host_training_lock.yaml',
    'inventory\hardware_inventory.yaml',
    'inventory\x5_dependency_lock.yaml',
    'delivery\STATUS.md',
    'delivery\TEST_MATRIX.md',
    'delivery\HANDOFF.md',
    'delivery\OFFLINE_VERIFICATION.md',
    'config\contracts\rpo_x5_candidate.json',
    'config\official_training_matrix.yaml',
    'config\navigation_offline_planner.yaml',
    'config\localization_offline_mapping.yaml',
    'scripts\board\probe_x5_dependencies.sh',
    'scripts\board\env_x5_deps.sh',
    'scripts\board\build_x5_dependencies.sh',
    'scripts\board\build_x5_official.sh',
    'scripts\board\capture_x5_dependency_evidence.sh',
    'scripts\board\run_x5_onnx_synthetic_probe.sh',
    'scripts\board\run_x5_navigation_offline_probe.sh',
    'scripts\board\run_x5_depth_pipeline_probe.sh',
    'scripts\board\run_x5_navigation_plan_probe.sh',
    'scripts\board\run_x5_localization_synthetic_probe.sh',
    'scripts\board\debug_x5_localization_shutdown.sh',
    'scripts\board\capture_x5_offline_prework_evidence.sh',
    'probes\onnx_runtime_probe.cpp',
    'probes\pcd_to_pgm_offline.py',
    'probes\navigation_static_audit.py',
    'probes\depth_pipeline_offline.py',
    'probes\navigation_plan_offline.py',
    'probes\localization_synthetic_offline.py',
    'scripts\host\audit_model_contract.py',
    'scripts\host\audit_onnx_contract.py',
    'scripts\host\audit_ros_dependencies.py',
    'scripts\host\build_joint_contract_candidate.py',
    'scripts\host\audit_official_task_registry.py',
    'scripts\host\capture_training_environment.py',
    'scripts\host\run_policy_finite_probe.py',
    'scripts\host\run_parkour_finite_probe.py',
    'scripts\host\run_onnx_backend_probe.py',
    'scripts\host\compare_x5_onnx_parity.py',
    'scripts\host\compare_x5_depth_pipeline.py',
    'scripts\host\run_official_isaac_acceptance.py',
    'scripts\host\verify_isaac_artifacts.py',
    'scripts\host\summarize_official_offline.py',
    'evidence\b0\model_contract.json',
    'evidence\b0\onnx_contract.json',
    'evidence\b0\ros_dependency_contract.json',
    'evidence\x5\phase_a\dependency_probe_20260828.txt',
    'evidence\x5\phase_a\dpkg_package_list_20260828.txt',
    'evidence\x5\phase_a\ros2_pkg_list_20260828.txt',
    'evidence\x5\dependencies\dependency_overlay_20260828T220013+0800.txt',
    'evidence\x5\dependencies\dependency_overlay_20260828T224108+0800.txt',
    'evidence\x5\onnx_runtime\onnx_synthetic_20260828T222405+0800.json',
    'evidence\x5\navigation_offline\20260828T223859+0800\map_ikdtree_x5_offline.pgm',
    'evidence\x5\navigation_offline\20260828T223859+0800\map_ikdtree_x5_offline.yaml',
    'evidence\x5\navigation_offline\20260828T223859+0800\navigation_static_audit.json',
    'evidence\x5\navigation_offline\20260828T223859+0800\pcd_to_pgm.log',
    'evidence\x5\navigation_offline\20260828T223859+0800\pcd_to_pgm_summary.json',
    'evidence\x5\prework\offline_prework_20260828T224305+0800.txt',
    'evidence\x5\localization_synthetic\2026-08-29T02-00-12+08-00\result.json',
    'evidence\host\official_offline_summary_latest.json',
    'evidence\host\isaac\20260829T075941+0800\aggregate.json',
    'evidence\host\isaac\20260829T075941+0800\artifact_verification.json',
    'evidence\host\isaac\20260829T075941+0800\export_backend_probe.json',
    'patches\librealsense-use-local-json.patch',
    'patches\roboparty-navigation-x5-build.patch',
    'patches\roboparty-navigation-x5-runtime.patch',
    'patches\roboparty-navigation-x5-thread-lifecycle.patch',
    'patches\roboparty-navigation-x5-ros-entity-lifecycle.patch',
    'patches\roboparty-navigation-x5-common-globals.patch',
    'patches\roboparty-deploy-x5-build.patch',
    'requirements\open3d-0.17.0.in',
    'requirements\open3d-0.17.0-aarch64.lock',
    'requirements\navigation-offline-x5.in',
    'requirements\navigation-offline-x5-aarch64.lock'
)

foreach ($relativePath in $requiredFiles) {
    Test-Condition -Condition (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath) -PathType Leaf) -Label "required file: $relativePath"
}

$evidenceHashes = [ordered]@{
    'evidence\x5\dependencies\dependency_overlay_20260828T220013+0800.txt' = 'dd1265d34b708a49d035a904123bbe6085c2a3d0476e7ca9780bc68f8d1ea765'
    'evidence\x5\dependencies\dependency_overlay_20260828T224108+0800.txt' = '910970aa66a03d9716283e374305ac4420a21b29ef367fcba14918803ecbf49b'
    'evidence\x5\onnx_runtime\onnx_synthetic_20260828T222405+0800.json' = 'bd4409420cfca18c3fd4eb5e10acbab24095eca49f8336c160a7180ad2e8a05a'
    'evidence\x5\navigation_offline\20260828T223859+0800\map_ikdtree_x5_offline.pgm' = '66fae06990e90202375bfb64f904f63348de26b86e146723562335ae88b9d308'
    'evidence\x5\navigation_offline\20260828T223859+0800\map_ikdtree_x5_offline.yaml' = '5a044dc91990f27f96e16e88ca34afe7f7c91fc24495df475e32f25f2adf6d9a'
    'evidence\x5\navigation_offline\20260828T223859+0800\navigation_static_audit.json' = '97ce9e09f4e0baecad00eb8f174e30a8c9d71c0ab48be9b2fd5a1d44386c5894'
    'evidence\x5\navigation_offline\20260828T223859+0800\pcd_to_pgm.log' = '03aa2319cd9e00df7c66aaca4fab99517d0db0bfea044d1233ffd7bb31b810a2'
    'evidence\x5\navigation_offline\20260828T223859+0800\pcd_to_pgm_summary.json' = 'ef985e9a305bd129e1b6353c237a41871477bf19aa8d9663207e7fb0cfb5e2af'
    'evidence\x5\prework\offline_prework_20260828T224305+0800.txt' = '712f2f74c09f332b4807c940b8d7a165fee263f1749997ca56a0aa0253c35106'
    'evidence\x5\localization_synthetic\2026-08-29T02-00-12+08-00\result.json' = 'b4a2201f3e0f5f10a3c53edcdf557957df760c230e849894a20f876ca2db9862'
}

foreach ($entry in $evidenceHashes.GetEnumerator()) {
    $evidencePath = Join-Path $projectRoot $entry.Key
    if (Test-Path -LiteralPath $evidencePath -PathType Leaf) {
        $actualHash = (Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
        Test-Condition -Condition ($actualHash -eq $entry.Value) -Label "evidence SHA-256: $($entry.Key)"
    }
}

$repositories = [ordered]@{
    'roboparty_deploy' = 'a8a0f1557cc5d085234b8bac248a8f543342f531'
    'roboparty_train' = '92008a6317d6d0efe8b58abc2fb3c630c75992c0'
    'roboparty_navigation' = 'd6ab9913599672af78422eb2e89a16c0460a8009'
    'rpo_description' = '37aac9ca665e92731444a1618320078e7ba21569'
    'GMR' = 'f7bfaead0d0b896e6b74a64bc6948ca8b09d420b'
}

foreach ($entry in $repositories.GetEnumerator()) {
    $repositoryPath = Join-Path $projectRoot "upstream\$($entry.Key)"
    Test-Condition -Condition (Test-Path -LiteralPath (Join-Path $repositoryPath '.git')) -Label "checkout exists: $($entry.Key)"
    if (Test-Path -LiteralPath (Join-Path $repositoryPath '.git')) {
        $actualCommit = (& git -C $repositoryPath rev-parse HEAD).Trim()
        Test-Condition -Condition ($actualCommit -eq $entry.Value) -Label "commit pin: $($entry.Key)"

        $dirty = @(& git -C $repositoryPath status --porcelain --untracked-files=no)
        Test-Condition -Condition ($dirty.Count -eq 0) -Label "tracked worktree clean: $($entry.Key)"
    }
}

$wrapperRepositories = @('roboparty_deploy', 'roboparty_train', 'roboparty_navigation')
foreach ($name in $wrapperRepositories) {
    $repositoryPath = Join-Path $projectRoot "upstream\$name"
    $submoduleStatus = @(& git -C $repositoryPath submodule status --recursive)
    $badSubmodules = @($submoduleStatus | Where-Object { $_ -match '^[+-U]' })
    Test-Condition -Condition ($badSubmodules.Count -eq 0) -Label "submodules initialized at pins: $name"
}

$ignoredUpstream = (& git -C $projectRoot check-ignore 'upstream/roboparty_deploy').Trim()
Test-Condition -Condition ($ignoredUpstream -eq 'upstream/roboparty_deploy') -Label 'upstream checkouts excluded from project ownership'

if ($failures.Count -gt 0) {
    Write-Host "RESULT FAIL ($($failures.Count) checks)"
    exit 1
}

Write-Host 'RESULT PASS (offline artifacts and captured X5 evidence integrity only; no algorithm or hardware validation implied)'
