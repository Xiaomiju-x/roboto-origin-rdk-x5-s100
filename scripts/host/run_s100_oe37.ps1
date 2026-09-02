param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$OnlyModel = '',
    [string]$Workspace = ''
)

$ErrorActionPreference = 'Stop'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$image = 'registry.d-robotics.cc/deliver/ai_toolchain_ubuntu_22_s100_s600_cpu:v3.7.0'
$artifactRoot = Join-Path $RepoRoot '.artifacts\openexplorer\3.7.0'
$archive = Join-Path $artifactRoot 'oe-package-3.7.0-s100-s600.tgz'
$extractRoot = Join-Path $artifactRoot 'extracted'
$oeRoot = Join-Path $extractRoot 'drobotics_s100_s600_open_explorer_v3.7.0'
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $workspace = Join-Path $RepoRoot 'build\s100_bpu'
} else {
    $workspace = (Resolve-Path -LiteralPath $Workspace).Path
}
$runner = Join-Path $RepoRoot 'scripts\host\run_s100_oe37_compile.sh'

if (-not (Test-Path -LiteralPath $docker -PathType Leaf)) { throw 'Docker CLI not found' }
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw 'OE 3.7.0 package not found' }
if ((Get-Item -LiteralPath $archive).Length -ne 2846971999) {
    throw "OE archive size mismatch: $((Get-Item -LiteralPath $archive).Length)"
}
if (-not (Test-Path -LiteralPath (Join-Path $workspace 'manifest.json') -PathType Leaf)) {
    throw 'Frozen S100 BPU manifest not found; run prepare_s100_bpu_assets.py first'
}

if (-not (Test-Path -LiteralPath (Join-Path $oeRoot 'run_docker.sh') -PathType Leaf)) {
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    # Extract only the host toolchain payload.  The full release also contains
    # Linux symlinks for board/runtime samples that Windows cannot materialize
    # and that are not needed for x86 ONNX-to-HBM compilation.
    & tar.exe -xzf $archive -C $extractRoot `
        './drobotics_s100_s600_open_explorer_v3.7.0/run_docker.sh' `
        './drobotics_s100_s600_open_explorer_v3.7.0/README-CN' `
        './drobotics_s100_s600_open_explorer_v3.7.0/README-EN' `
        './drobotics_s100_s600_open_explorer_v3.7.0/package/host/ai_toolchain'
    if ($LASTEXITCODE -ne 0) { throw 'OE package extraction failed' }
}

$runtimeWheel = Join-Path $oeRoot 'package\host\ai_toolchain\hbm_infer-3.13.6-py3-none-any.whl'
$compilerWheel = Join-Path $oeRoot 'package\host\ai_toolchain\hbdk4_compiler-4.7.5-cp310-cp310-manylinux_2_17_x86_64.whl'
if (-not (Test-Path -LiteralPath $runtimeWheel -PathType Leaf)) { throw 'Expected hbm_infer 3.13.6 wheel missing' }
if (-not (Test-Path -LiteralPath $compilerWheel -PathType Leaf)) { throw 'Expected HBDK 4.7.5 compiler wheel missing' }

& $docker image inspect $image | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'OE 3.7.0 CPU image is not loaded' }

$oeMount = $oeRoot -replace '\\','/'
$workspaceMount = $workspace -replace '\\','/'
$repoMount = $RepoRoot -replace '\\','/'
& $docker run --rm `
    --name roboto-s100-oe37 `
    --network none `
    --mount "type=bind,src=$oeMount,dst=/open_explorer,readonly" `
    --mount "type=bind,src=$workspaceMount,dst=/workspace" `
    --mount "type=bind,src=$repoMount,dst=/project,readonly" `
    --workdir /workspace `
    --env "S100_OE_ONLY=$OnlyModel" `
    $image `
    bash /project/scripts/host/run_s100_oe37_compile.sh
if ($LASTEXITCODE -ne 0) { throw "S100 OE compilation failed with exit code $LASTEXITCODE" }

Get-Content -LiteralPath (Join-Path $workspace 'logs\conversion_summary.json')
