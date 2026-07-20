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
        throw "Refusing filesystem operation outside Phase 3 root: $Child"
    }
}

function Invoke-StudyCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    Write-Host "`n== $Name ==" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is unavailable."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Start Docker Desktop and wait for its engine before running Phase 3."
}
docker image inspect "strategy-lab-vectorbt:0.28.5-phase1" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The pinned VectorBT image is missing. Re-run the Phase 1 proof first."
}

Assert-ChildPath -Parent $root -Child $artifacts
if (Test-Path -LiteralPath $artifacts) {
    Remove-Item -LiteralPath $artifacts -Recurse -Force
}
New-Item -ItemType Directory -Path $artifacts | Out-Null

Push-Location $root
try {
    Invoke-StudyCommand "Pandas reference study" {
        docker compose run --rm pandas-reference
    }
    Invoke-StudyCommand "Pinned VectorBT study" {
        docker compose run --rm vectorbt-study
    }
    Invoke-StudyCommand "Cross-engine Phase 3 parity" {
        docker compose run --rm compare-results
    }
    Write-Host "`nPhase 3 runtime proof passed." -ForegroundColor Green
    Write-Host "Artifacts: $artifacts"
}
finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose down --remove-orphans *> $null
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}
