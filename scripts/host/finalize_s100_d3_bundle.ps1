param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$SourceWorkspace = '',
    [string]$SweepResult = '',
    [string]$DestinationWorkspace = '',
    [string]$LockPath = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($SourceWorkspace)) {
    $SourceWorkspace = Join-Path $RepoRoot 'build\s100_bpu_mixed_final'
}
if ([string]::IsNullOrWhiteSpace($SweepResult)) {
    $SweepResult = Join-Path $RepoRoot 'evidence\s100\board_d3\attn_sweep\2026-08-29T17-10-31+08-00\result.json'
}
if ([string]::IsNullOrWhiteSpace($DestinationWorkspace)) {
    $DestinationWorkspace = Join-Path $RepoRoot 'build\s100_bpu_final'
}
if ([string]::IsNullOrWhiteSpace($LockPath)) {
    $LockPath = Join-Path $RepoRoot 'config\s100_d3_deployment_lock.json'
}

$SourceWorkspace = (Resolve-Path -LiteralPath $SourceWorkspace).Path
$SweepResult = (Resolve-Path -LiteralPath $SweepResult).Path
if (Test-Path -LiteralPath $DestinationWorkspace) {
    throw "Destination workspace already exists: $DestinationWorkspace"
}

$sourceManifestPath = Join-Path $SourceWorkspace 'manifest.json'
$conversionSummaryPath = Join-Path $SourceWorkspace 'logs\conversion_summary.json'
if (-not (Test-Path -LiteralPath $sourceManifestPath -PathType Leaf)) {
    throw 'Source manifest is missing'
}
if (-not (Test-Path -LiteralPath $conversionSummaryPath -PathType Leaf)) {
    throw 'Source conversion summary is missing'
}

$manifest = Get-Content -LiteralPath $sourceManifestPath -Raw | ConvertFrom-Json
$sweep = Get-Content -LiteralPath $SweepResult -Raw | ConvertFrom-Json
if ($sweep.result -ne 'PASS' -or $sweep.decision.deployment -ne 'CPU_FALLBACK') {
    throw 'The precision sweep did not authorize a CPU fallback'
}
if ($sweep.model -ne 'policy_attn_enc') {
    throw "Unexpected sweep model: $($sweep.model)"
}
$attnSpec = $manifest.models | Where-Object { $_.name -eq 'policy_attn_enc' }
if ($null -eq $attnSpec -or $attnSpec.Count -ne 1) {
    throw 'Expected exactly one policy_attn_enc manifest entry'
}
if ($attnSpec.onnx_sha256 -ne $sweep.model_sha256) {
    throw 'Sweep model hash does not match the frozen manifest'
}
if ($attnSpec.test_input_sha256 -ne $sweep.test_input_sha256) {
    throw 'Sweep fixture hash does not match the frozen manifest'
}
if (($sweep.candidates | Where-Object { $_.status -eq 'PASS' }).Count -ne 0) {
    throw 'A passing BPU candidate exists; CPU fallback must not be selected'
}
foreach ($check in $sweep.cpu.checks.psobject.Properties) {
    if (-not [bool]$check.Value) {
        throw "CPU fallback check failed: $($check.Name)"
    }
}

$sweepRelative = [IO.Path]::GetRelativePath($RepoRoot, $SweepResult).Replace('\', '/')
$sweepHash = (Get-FileHash -LiteralPath $SweepResult -Algorithm SHA256).Hash.ToLowerInvariant()
$rejected = @(
    $sweep.candidates | ForEach-Object {
        [ordered]@{
            label = $_.label
            sha256 = $_.sha256
            bytes = $_.bytes
            status = $_.status
            checks = $_.checks
            cosine_mean = $_.cosine_mean
            cosine_min = $_.cosine_min
            max_abs_error = $_.max_abs_error
            direction_agreement = $_.direction_agreement
            bpu_runtime_api_timing = $_.bpu_runtime_api_timing
        }
    }
)
$lock = [ordered]@{
    schema_version = 1
    finalized_at = (Get-Date).ToString('o')
    scope = 'S100 D3 single-board offline deployment decision; no robot or external peripherals'
    source_evidence = $sweepRelative
    source_evidence_sha256 = $sweepHash
    model = 'policy_attn_enc'
    deployment = 'CPU_FALLBACK'
    reason = $sweep.decision.reason
    cpu = $sweep.cpu
    rejected_bpu_candidates = $rejected
    required_bpu_models = @('encoder', 'policy')
    external_device_access = $false
    control_output = $false
    result = 'PASS'
}
$lockParent = Split-Path -Parent $LockPath
New-Item -ItemType Directory -Force -Path $lockParent | Out-Null
$lock | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $LockPath -Encoding utf8NoBOM
$lockHash = (Get-FileHash -LiteralPath $LockPath -Algorithm SHA256).Hash.ToLowerInvariant()

Copy-Item -LiteralPath $SourceWorkspace -Destination $DestinationWorkspace -Recurse
Copy-Item -LiteralPath $LockPath -Destination (Join-Path $DestinationWorkspace 'deployment_lock.json')
$bundleEvidence = Join-Path $DestinationWorkspace 'evidence'
New-Item -ItemType Directory -Force -Path $bundleEvidence | Out-Null
Copy-Item -LiteralPath $SweepResult -Destination (Join-Path $bundleEvidence 'policy_attn_enc_precision_sweep.json')

foreach ($model in $manifest.models) {
    $model | Add-Member -NotePropertyName deployment -NotePropertyValue 'BPU' -Force
    if ($model.name -eq 'policy_attn_enc') {
        $model.deployment = 'CPU_FALLBACK'
        $model | Add-Member -NotePropertyName deployment_reason -NotePropertyValue $sweep.decision.reason -Force
        $model | Add-Member -NotePropertyName rejected_bpu_candidates -NotePropertyValue $rejected -Force
    }
}
$manifest | Add-Member -NotePropertyName deployment_finalized_at -NotePropertyValue (Get-Date).ToString('o') -Force
$manifest | Add-Member -NotePropertyName deployment_lock -NotePropertyValue ([ordered]@{
    file = 'deployment_lock.json'
    sha256 = $lockHash
    source_evidence = 'evidence/policy_attn_enc_precision_sweep.json'
    source_evidence_sha256 = $sweepHash
    finalizer = 'scripts/host/finalize_s100_d3_bundle.ps1'
    finalizer_sha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
}) -Force
$manifest.result = 'DEPLOYMENT_FROZEN'
$destinationManifest = Join-Path $DestinationWorkspace 'manifest.json'
$manifest | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $destinationManifest -Encoding utf8NoBOM

[ordered]@{
    destination = $DestinationWorkspace
    manifest_sha256 = (Get-FileHash -LiteralPath $destinationManifest -Algorithm SHA256).Hash.ToLowerInvariant()
    deployment_lock_sha256 = $lockHash
    policy_attn_enc_deployment = 'CPU_FALLBACK'
} | ConvertTo-Json
