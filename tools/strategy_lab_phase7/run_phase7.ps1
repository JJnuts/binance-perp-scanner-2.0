$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$artifacts = [System.IO.Path]::GetFullPath((Join-Path $root "artifacts"))

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $prefix = $Parent.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $Child.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem operation outside Phase 7 root: $Child"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is unavailable."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Start Docker Desktop and wait until its engine is running."
}
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
docker image inspect "strategy-lab-vectorbt:0.28.5-phase1" *> $null
$imageAvailable = $LASTEXITCODE -eq 0
$ErrorActionPreference = $previousErrorActionPreference
if (-not $imageAvailable) {
    throw @"
The pinned VectorBT image is missing. Restore only that image with:
  cd "$(Join-Path $root '..\strategy_lab_phase1')"
  docker compose build vectorbt-proof
Then return to Phase 7 and rerun this script.
"@
}

Add-Type -AssemblyName Microsoft.VisualBasic
$computerInfo = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
$availableGb = [math]::Round($computerInfo.AvailablePhysicalMemory / 1GB, 1)
if ($availableGb -lt 0.8) {
    throw "Only $availableGb GB RAM is available. Free at least 0.8 GB."
}

Assert-ChildPath -Parent $root -Child $artifacts
if (Test-Path -LiteralPath $artifacts) {
    Remove-Item -LiteralPath $artifacts -Recurse -Force
}
New-Item -ItemType Directory -Path $artifacts | Out-Null

Push-Location $root
try {
    Write-Host "`n== Phase 7 prompt engine proof ==" -ForegroundColor Cyan
    docker compose run --rm prompt-proof
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 7 prompt proof failed with exit code $LASTEXITCODE"
    }
    Write-Host "`nPhase 7 runtime proof passed." -ForegroundColor Green
    Write-Host "Artifacts: $artifacts"
}
finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose down --remove-orphans 2>&1 | Out-Null
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}
