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
        throw "Refusing filesystem operation outside Phase 9 root: $Child"
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
docker image inspect "strategy-lab-streamlit:1.59.2-phase8" *> $null
$phase8ImageAvailable = $LASTEXITCODE -eq 0
$ErrorActionPreference = $previousErrorActionPreference
if (-not $phase8ImageAvailable) {
    throw @"
The pinned Phase 8 Streamlit image is missing. Restore it with:
  cd "$(Join-Path $root '..\strategy_lab_phase8')"
  docker compose build ui-proof
Then return to Phase 9 and rerun this script.
"@
}

Add-Type -AssemblyName Microsoft.VisualBasic
$computerInfo = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
$availableGb = [math]::Round($computerInfo.AvailablePhysicalMemory / 1GB, 1)
if ($availableGb -lt 1.2) {
    throw "Only $availableGb GB RAM is available. Close applications until at least 1.2 GB is free."
}

Assert-ChildPath -Parent $root -Child $artifacts
if (Test-Path -LiteralPath $artifacts) {
    Remove-Item -LiteralPath $artifacts -Recurse -Force
}
New-Item -ItemType Directory -Path $artifacts | Out-Null

Push-Location $root
try {
    Write-Host "`n== Phase 9 final offline stress proof ==" -ForegroundColor Cyan
    docker compose run --rm stress-proof
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 9 stress proof failed with exit code $LASTEXITCODE"
    }
    Write-Host "`nPhase 9 runtime proof passed." -ForegroundColor Green
    Write-Host "Artifacts: $artifacts"
}
finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose down --remove-orphans 2>&1 | Out-Null
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}
