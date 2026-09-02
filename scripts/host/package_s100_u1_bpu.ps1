param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,
    [Parameter(Mandatory = $true)]
    [string]$X5Fixture,
    [Parameter(Mandatory = $true)]
    [string]$X5Evidence,
    [string]$OutputRoot = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'build\s100_u1_deploy')
)

$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Copy-LockedFile([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Missing source file: $Source"
    }
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
    if ((Get-Sha256 $Source) -ne (Get-Sha256 $Destination)) {
        throw "Copy hash mismatch: $Source"
    }
}

$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$fixturePath = (Resolve-Path -LiteralPath $X5Fixture).Path
$x5EvidencePath = (Resolve-Path -LiteralPath $X5Evidence).Path
$sourceManifestPath = Join-Path $workspacePath 'manifest.json'
$conversionPath = Join-Path $workspacePath 'logs\conversion_summary.json'
$sourceManifest = Get-Content -Raw -LiteralPath $sourceManifestPath | ConvertFrom-Json
$conversion = Get-Content -Raw -LiteralPath $conversionPath | ConvertFrom-Json
$modelSpec = @($sourceManifest.models | Where-Object name -eq 'u1_temporal')
$compileSpec = @($conversion.models | Where-Object name -eq 'u1_temporal')
if ($modelSpec.Count -ne 1) { throw 'Expected exactly one u1_temporal source manifest entry' }
if ($compileSpec.Count -ne 1 -or $compileSpec[0].status -ne 'PASS') {
    throw 'U1 conversion summary is not PASS'
}
if (@($compileSpec[0].artifacts).Count -ne 1) { throw 'Expected exactly one U1 HBM artifact' }

$onnxSource = Join-Path $workspacePath 'models\u1_temporal.onnx'
$testSource = Join-Path $workspacePath 'test\u1_temporal\inputs.npy'
$hbmSource = Join-Path $workspacePath ($compileSpec[0].artifacts[0].path -replace '/', '\')
$x5EvidenceData = Get-Content -Raw -LiteralPath $x5EvidencePath | ConvertFrom-Json
if ((Get-Sha256 $onnxSource) -ne $modelSpec[0].onnx_sha256) { throw 'Source ONNX hash mismatch' }
if ((Get-Sha256 $testSource) -ne $modelSpec[0].test_input_sha256) { throw 'Source test hash mismatch' }
if ((Get-Sha256 $hbmSource) -ne $compileSpec[0].artifacts[0].sha256) { throw 'Compiled HBM hash mismatch' }
if ((Get-Sha256 $fixturePath) -ne $x5EvidenceData.vectors.sha256) { throw 'X5 fixture hash mismatch' }
if ($x5EvidenceData.model.sha256 -ne $modelSpec[0].onnx_sha256) { throw 'X5 model hash differs from U1 source model' }
if ($x5EvidenceData.result -ne 'PASS') { throw 'X5 U1 baseline evidence is not PASS' }

$stamp = Get-Date -Format 'yyyyMMddTHHmmsszzz'
$stamp = $stamp -replace ':', ''
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$stage = Join-Path $OutputRoot $stamp
if (Test-Path -LiteralPath $stage) { throw "Refusing to overwrite stage: $stage" }
New-Item -ItemType Directory -Path $stage | Out-Null

$destinations = [ordered]@{
    onnx = 'models/u1_temporal.onnx'
    test_inputs = 'test/u1_temporal/inputs.npy'
    x5_fixture = 'reference/x5_probe_input.npz'
    hbm = 'compiled/u1_temporal/u1_temporal_nashe.hbm'
    source_manifest = 'metadata/source_manifest.json'
    conversion_summary = 'metadata/conversion_summary.json'
    x5_baseline_evidence = 'metadata/x5_temporal_onnx.json'
    compiler_config = 'metadata/u1_temporal_nashe.yaml'
    compiler_log = 'metadata/compile_u1_temporal.log'
    toolchain_versions = 'metadata/toolchain_versions.txt'
}
$sources = [ordered]@{
    onnx = $onnxSource
    test_inputs = $testSource
    x5_fixture = $fixturePath
    hbm = $hbmSource
    source_manifest = $sourceManifestPath
    conversion_summary = $conversionPath
    x5_baseline_evidence = $x5EvidencePath
    compiler_config = (Join-Path $workspacePath 'configs\u1_temporal_nashe.yaml')
    compiler_log = (Join-Path $workspacePath 'logs\compile_u1_temporal.log')
    toolchain_versions = (Join-Path $workspacePath 'logs\toolchain_versions.txt')
}
$artifacts = [ordered]@{}
foreach ($key in $destinations.Keys) {
    $destination = Join-Path $stage ($destinations[$key] -replace '/', '\')
    Copy-LockedFile $sources[$key] $destination
    $artifacts[$key] = [ordered]@{
        path = $destinations[$key]
        bytes = (Get-Item -LiteralPath $destination).Length
        sha256 = Get-Sha256 $destination
    }
}

$deploymentManifest = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToString('o')
    scope = 'S100 U1 temporal BEV offline CPU/BPU deployment bundle; no robot or peripherals'
    target = [ordered]@{
        board = 'RDK S100'
        march = 'nash-e'
        toolchain = 'OpenExplorer 3.7.0'
        runtime = 'UCP 3.13.6'
    }
    source = [ordered]@{
        model = 'project-owned U1 temporal occupancy/flow extension'
        model_sha256 = $modelSpec[0].onnx_sha256
        test_count = [int]$modelSpec[0].test_count
        test_seed = [int]$modelSpec[0].calibration_seed
    }
    x5_baseline = [ordered]@{
        result = $x5EvidenceData.result
        fixture_sha256 = $x5EvidenceData.vectors.sha256
        model_sha256 = $x5EvidenceData.model.sha256
        evidence_sha256 = Get-Sha256 $x5EvidencePath
    }
    acceptance = [ordered]@{
        cosine_mean_min = 0.999
        per_sample_output_cosine_min = 0.99
        warmups_min = 20
        measurements_min = 100
    }
    artifacts = $artifacts
    external_network_during_compile = $false
    external_device_access = $false
    control_output = $false
}
$deploymentManifestPath = Join-Path $stage 'deployment_manifest.json'
$deploymentManifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $deploymentManifestPath -Encoding utf8

$archive = "$stage.tar.gz"
& tar.exe -czf $archive -C $stage .
if ($LASTEXITCODE -ne 0) { throw 'Failed to create U1 deployment archive' }
$summary = [ordered]@{
    stage = $stage
    stage_file_count = @(Get-ChildItem -LiteralPath $stage -Recurse -File).Count
    deployment_manifest_sha256 = Get-Sha256 $deploymentManifestPath
    archive = $archive
    archive_bytes = (Get-Item -LiteralPath $archive).Length
    archive_sha256 = Get-Sha256 $archive
    result = 'PASS'
}
$summary | ConvertTo-Json -Depth 5
