param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'
if (-not $OutputPath) {
    $OutputPath = Join-Path $RepoRoot 'evidence\host\s100_offline_summary_latest.json'
}
$evidenceRoot = Join-Path $RepoRoot 'evidence\s100'

function Read-JsonFile([string]$Path) {
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Find-LatestFile([string]$Segment, [string]$Name) {
    $matches = Get-ChildItem -LiteralPath $evidenceRoot -Recurse -File -Filter $Name |
        Where-Object { $_.FullName -match [regex]::Escape($Segment) } |
        Sort-Object FullName
    if (-not $matches) {
        throw "Missing $Name for segment: $Segment"
    }
    $matches[-1].FullName
}

function Find-LatestResult([string]$Segment) {
    Find-LatestFile $Segment 'result.json'
}

function Relative-Path([string]$Path) {
    $rootUri = [System.Uri]::new(($RepoRoot.TrimEnd('\') + '\'))
    $pathUri = [System.Uri]::new($Path)
    [System.Uri]::UnescapeDataString($rootUri.MakeRelativeUri($pathUri).ToString())
}

function Evidence-Record([string]$Path) {
    [ordered]@{
        path = Relative-Path $Path
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$paths = [ordered]@{
    d0 = Join-Path $evidenceRoot 'd0_readonly_snapshot_20260829.json'
    d1 = Join-Path $evidenceRoot 'board_d0_d2\evidence\d1_bootstrap_receipt.json'
    d2 = Join-Path $evidenceRoot 'board_d0_d2\evidence\d2_summary.json'
    d3 = Find-LatestResult 'd3_bpu_ab'
    d4_official = Find-LatestResult 'd4_official_smoke'
    d4_frontier = Find-LatestResult 'd4_frontier_audit'
    d5_nav = Find-LatestResult 'd5_nav2_multigoal'
    d5_chain = Find-LatestResult 'd5_chain_stress'
    u1_bpu = Find-LatestResult 'u1_bpu_ab'
    u1_manifest = Find-LatestFile 'u1_bpu_bundle' 'deployment_manifest.json'
    d6 = Find-LatestResult 'd6_clean_rebuild'
}
foreach ($entry in $paths.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value)) {
        throw "Missing evidence $($entry.Key): $($entry.Value)"
    }
}

$data = [ordered]@{}
foreach ($entry in $paths.GetEnumerator()) {
    $data[$entry.Key] = Read-JsonFile $entry.Value
}
$pass = [ordered]@{
    D0 = $data.d0.result -eq 'PASS'
    D1 = $data.d1.status -eq 'PASS'
    D2 = $data.d2.result -eq 'PASS'
    D3 = $data.d3.result -eq 'PASS' -and $data.d3.summary.fail -eq 0 -and $data.d3.summary.required_bpu_models_pass -and $data.u1_bpu.result -eq 'PASS'
    D4 = $data.d4_official.result -eq 'PASS' -and $data.d4_official.summary.pass -eq 4 -and $data.d4_frontier.result -eq 'PASS'
    D5 = $data.d5_nav.status -eq 'PASS' -and $data.d5_nav.goal_summary.passed -ge 20 -and $data.d5_chain.result -eq 'PASS'
    D6 = $data.d6.result -eq 'PASS'
}
$offlineGate = $pass.D0 -and $pass.D1 -and $pass.D2 -and $pass.D3 -and $pass.D4 -and $pass.D5
$deliveryComplete = $offlineGate -and $pass.D6

$evidence = [ordered]@{}
foreach ($entry in $paths.GetEnumerator()) {
    $evidence[$entry.Key] = Evidence-Record $entry.Value
}

$summary = [ordered]@{
    schema_version = 2
    generated_at = (Get-Date).ToString('o')
    scope = 'S100 single-board offline algorithm deployment readiness; no robot or external peripheral validation'
    board = [ordered]@{
        product = 'RDK S100'
        soc = 'S100'
        bpu = 'Nash-e'
        int8_tops = 80
        rdk_os = '4.0.5-Beta'
        ucp_runtime = '3.13.6'
        ros_domain_id = 42
        network_endpoints = 'redacted_in_public_summary'
    }
    gates = $pass
    s100_offline_gate_complete = $offlineGate
    delivery_complete = $deliveryComplete
    evidence = $evidence
    highlights = [ordered]@{
        d3 = $data.d3.summary
        u1_bpu = [ordered]@{
            hbm_sha256 = $data.u1_manifest.artifacts.hbm.sha256
            test_count = $data.u1_bpu.test_count
            cosine_global_mean = $data.u1_bpu.cosine_global_mean
            cosine_global_min = $data.u1_bpu.cosine_global_min
            cpu_p50_ms = $data.u1_bpu.s100_cpu_timing.p50_ms
            bpu_p50_ms = $data.u1_bpu.s100_bpu_runtime_api_timing.p50_ms
            bpu_p99_ms = $data.u1_bpu.s100_bpu_runtime_api_timing.p99_ms
            same_x5_fixture = $data.u1_bpu.checks.same_fixture_hash_as_x5 -and $data.u1_bpu.checks.x5_fixture_is_test_zero_exact
        }
        d4_official = $data.d4_official.summary
        d5_nav = $data.d5_nav.goal_summary
        d5_chain = [ordered]@{
            policy_steps = $data.d5_chain.chain.policy_steps
            guard_steps = $data.d5_chain.chain.guard_steps
            fault_count = $data.d5_chain.chain.faults.Count
            stress_elapsed_seconds = $data.d5_chain.stress.elapsed_seconds
            stress_invocations = $data.d5_chain.stress.timing.count
            stress_errors = $data.d5_chain.stress.errors.Count
            bpu_temperature_c_max = $data.d5_chain.stress.resource_summary.bpu_temperature_c_max
        }
        d6 = $data.d6.replays
    }
    deployment_decisions = [ordered]@{
        required_bpu = @('encoder', 'policy')
        upgrade_bpu = @('u1_temporal')
        cpu_fallback = @('policy_attn_enc')
        d4_deployed = @('YOLO26 detection', 'ByteTrack', 'Depth Anything V2', 'PointNet part segmentation')
        optional_frontier = 'Commit-locked and audited; blocked/reference candidates are not reported as deployed.'
    }
    limitations = @(
        'No camera, lidar, IMU, CAN, serial, motor, actuator, or robot was connected or accessed.',
        'No physical navigation, localization, locomotion, manipulation, or emergency-stop behavior is validated.',
        'The D5 policy-to-guard adapter uses tanh only for a normalized file-sink rehearsal; it is not a Roboto actuator mapping.',
        'Robot URDF/actuator identity, joint signs, zero offsets, limits, observation/action ordering, and control transport remain future hardware gates.',
        'Thermal and timing results describe this single-board no-peripheral setup only.'
    )
}

$parent = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$summary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputPath -Encoding utf8
$hash = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
[pscustomobject]@{
    output = $OutputPath
    sha256 = $hash
    s100_offline_gate_complete = $offlineGate
    delivery_complete = $deliveryComplete
} | ConvertTo-Json -Compress

if (-not $deliveryComplete) {
    exit 1
}
