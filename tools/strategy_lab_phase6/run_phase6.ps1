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
        throw "Refusing filesystem operation outside Phase 6 root: $Child"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is unavailable."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Start Docker Desktop and wait until its engine is running."
}
docker image inspect "strategy-lab-vectorbt:0.28.5-phase1" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The pinned VectorBT image is missing. Re-run Phase 1 first."
}

$phase5Manifest = Join-Path $root "..\strategy_lab_phase5\artifacts\run-manifest.json"
if (-not (Test-Path -LiteralPath $phase5Manifest)) {
    throw "Phase 5 artifacts are missing. Run Phase 5 before Phase 6."
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
    Write-Host "`n== Phase 6 deterministic results and exports ==" -ForegroundColor Cyan
    docker compose run --rm export-proof
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 6 export proof failed with exit code $LASTEXITCODE"
    }
    Write-Host "`nPhase 6 runtime proof passed." -ForegroundColor Green
    Write-Host "Artifacts: $artifacts"
}
finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose down --remove-orphans 2>&1 | Out-Null
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}
