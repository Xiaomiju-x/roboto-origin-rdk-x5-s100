param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$ModelZooRoot = '',
    [string]$DownloadedAssets = '',
    [string]$Destination = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ModelZooRoot)) {
    $ModelZooRoot = Join-Path $RepoRoot 'third_party\src\rdk_model_zoo_s'
}
if ([string]::IsNullOrWhiteSpace($DownloadedAssets)) {
    $DownloadedAssets = Join-Path $RepoRoot 'build\s100_d4_official_assets'
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $RepoRoot 'build\s100_d4_official_bundle'
}

$ModelZooRoot = (Resolve-Path -LiteralPath $ModelZooRoot).Path
$DownloadedAssets = (Resolve-Path -LiteralPath $DownloadedAssets).Path
if (Test-Path -LiteralPath $Destination) {
    throw "Destination already exists: $Destination"
}

$commit = (& git -C $ModelZooRoot rev-parse HEAD).Trim()
$branch = (& git -C $ModelZooRoot branch --show-current).Trim()
$remote = (& git -C $ModelZooRoot remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $commit.Length -ne 40) {
    throw 'Unable to lock the Model Zoo source commit'
}
if ((& git -C $ModelZooRoot status --porcelain).Count -ne 0) {
    throw 'Model Zoo source checkout is not clean'
}

$zooDestination = Join-Path $Destination 'rdk_model_zoo'
$modelDestination = Join-Path $Destination 'models'
$dataDestination = Join-Path $Destination 'data'
$runnerDestination = Join-Path $Destination 'runner'
New-Item -ItemType Directory -Path $zooDestination -Force | Out-Null
New-Item -ItemType Directory -Path $modelDestination -Force | Out-Null
New-Item -ItemType Directory -Path $dataDestination -Force | Out-Null
New-Item -ItemType Directory -Path $runnerDestination -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'LICENSE') -Destination $zooDestination
Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'utils') -Destination $zooDestination -Recurse

$sampleNames = @('ultralytics_yolo26', 'bytetrack', 'depth_anything_v2', 'pointnet')
foreach ($sampleName in $sampleNames) {
    $sampleSource = Join-Path $ModelZooRoot "samples\vision\$sampleName"
    $sampleDestination = Join-Path $zooDestination "samples\vision\$sampleName"
    New-Item -ItemType Directory -Path $sampleDestination -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sampleSource 'README.md') -Destination $sampleDestination
    Copy-Item -LiteralPath (Join-Path $sampleSource 'README_cn.md') -Destination $sampleDestination
    New-Item -ItemType Directory -Path (Join-Path $sampleDestination 'runtime') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sampleSource 'runtime\python') -Destination (Join-Path $sampleDestination 'runtime') -Recurse
}
Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'samples\vision\bytetrack\3rdparty') -Destination (Join-Path $zooDestination 'samples\vision\bytetrack') -Recurse

$modelFiles = @(
    'yolo26n_detect_nashe_640x640_nv12.hbm',
    'yolov5x_672x672_nv12.hbm',
    'depth_any.hbm',
    'pointnet.hbm'
)
foreach ($file in $modelFiles) {
    Copy-Item -LiteralPath (Join-Path $DownloadedAssets $file) -Destination $modelDestination
}
Copy-Item -LiteralPath (Join-Path $DownloadedAssets 'track_test.mp4') -Destination $dataDestination
Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'samples\vision\ultralytics_yolo26\test_data\bus.jpg') -Destination $dataDestination
Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'samples\vision\ultralytics_yolo26\test_data\coco_classes.names') -Destination $dataDestination
Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'samples\vision\depth_anything_v2\test_data\furseal.jpg') -Destination $dataDestination
Copy-Item -LiteralPath (Join-Path $ModelZooRoot 'samples\vision\pointnet\test_data\chair.pts') -Destination $dataDestination
Copy-Item -LiteralPath (Join-Path $RepoRoot 'scripts\board\run_s100_d4_official_smoke.py') -Destination $runnerDestination
Copy-Item -LiteralPath (Join-Path $RepoRoot 'scripts\board\run_s100_d4_official_smoke.sh') -Destination $runnerDestination

$downloads = @(
    [ordered]@{
        file = 'models/yolo26n_detect_nashe_640x640_nv12.hbm'
        url = 'https://archive.d-robotics.cc/downloads/rdk_model_zoo/rdk_s100/Ultralytics_YOLO_OE_3.7.0/nash-e/yolo26n_detect_nashe_640x640_nv12.hbm'
        server_content_length = 7926296
        server_last_modified = '2026-02-24T08:43:48Z'
    },
    [ordered]@{
        file = 'models/yolov5x_672x672_nv12.hbm'
        url = 'https://archive.d-robotics.cc/downloads/rdk_model_zoo/rdk_s100/ultralytics_YOLO/yolov5x_672x672_nv12.hbm'
        server_content_length = 95032776
        server_last_modified = '2025-12-04T11:49:52Z'
    },
    [ordered]@{
        file = 'data/track_test.mp4'
        url = 'https://archive.d-robotics.cc/downloads/rdk_model_zoo/rdk_s100/ByteTrack/track_test.mp4'
        server_content_length = 5909113
        server_last_modified = '2025-05-26T07:35:26Z'
    },
    [ordered]@{
        file = 'models/depth_any.hbm'
        url = 'https://archive.d-robotics.cc/downloads/rdk_model_zoo/rdk_s100/depth_any/depth_any.hbm'
        server_content_length = 121824312
        server_last_modified = '2025-09-25T12:36:45Z'
    },
    [ordered]@{
        file = 'models/pointnet.hbm'
        url = 'https://archive.d-robotics.cc/downloads/rdk_model_zoo/rdk_s100/PointNet/pointnet.hbm'
        server_content_length = 2903224
        server_last_modified = '2026-06-22T07:24:05Z'
    }
)
foreach ($download in $downloads) {
    $downloadPath = Join-Path $Destination $download.file
    if ((Get-Item -LiteralPath $downloadPath).Length -ne $download.server_content_length) {
        throw "Official asset length mismatch: $($download.file)"
    }
    $download.sha256 = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

$assets = @(
    Get-ChildItem -LiteralPath $Destination -Recurse -File |
        Sort-Object FullName |
        ForEach-Object {
            [ordered]@{
                path = [IO.Path]::GetRelativePath($Destination, $_.FullName).Replace('\', '/')
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
)
$manifest = [ordered]@{
    schema_version = 1
    created_at = (Get-Date).ToString('o')
    scope = 'S100 D4 official offline Model Zoo smoke; no external device or control I/O'
    source = [ordered]@{
        repository = $remote
        branch = $branch
        commit = $commit
        license = 'Apache-2.0'
        license_sha256 = (Get-FileHash -LiteralPath (Join-Path $ModelZooRoot 'LICENSE') -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    official_downloads = $downloads
    adapters = @(
        'Depth Anything V2 keeps official HBM preprocessing and replaces only torch.interpolate postprocessing with OpenCV bilinear resize.',
        'ByteTrack keeps the official tracker and detector code; project-local SciPy/NumPy adapters replace optional lap and cython_bbox extensions.',
        'No upstream checkout file is modified.'
    )
    smoke = @('YOLO26 detect', 'ByteTrack', 'Depth Anything V2', 'PointNet part segmentation')
    assets = $assets
    external_device_access = $false
    control_output = $false
    result = 'ASSETS_FROZEN'
}
$manifestPath = Join-Path $Destination 'manifest.json'
$manifest | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

[ordered]@{
    destination = $Destination
    source_commit = $commit
    asset_count = $assets.Count
    manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json
