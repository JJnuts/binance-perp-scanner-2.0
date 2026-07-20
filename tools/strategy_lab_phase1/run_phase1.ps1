$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$artifacts = [System.IO.Path]::GetFullPath((Join-Path $root "artifacts"))
$results = [System.IO.Path]::GetFullPath(
    (Join-Path $root "freqtrade\user_data\backtest_results")
)

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )

    $parentWithSeparator = $Parent.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar

    if (-not $Child.StartsWith($parentWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem operation outside Phase 1 root: $Child"
    }
}

function Reset-GeneratedDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-ChildPath -Parent $root -Child $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
}

function Invoke-ProofCommand {
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

function Assert-BacktestResult {
    $lastResult = Join-Path $results ".last_result.json"
    if (-not (Test-Path -LiteralPath $lastResult -PathType Leaf)) {
        throw "Freqtrade did not create .last_result.json. Review the Freqtrade output above for the underlying error."
    }
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is unavailable. Run 'wsl.exe --install --no-distribution' as Administrator and restart."
}

$wslStatus = & wsl.exe --status 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "WSL is not ready. Complete the WSL installation and restart Windows.`n$wslStatus"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is unavailable. Install and start Docker Desktop with the WSL2 backend."
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is installed but its engine is not ready."
}

Add-Type -AssemblyName Microsoft.VisualBasic
$computerInfo = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
$availableGb = [math]::Round($computerInfo.AvailablePhysicalMemory / 1GB, 1)
if ($availableGb -lt 3.0) {
    throw "Only $availableGb GB RAM is available. Close other applications until at least 3 GB is free."
}

Reset-GeneratedDirectory -Path $artifacts
Reset-GeneratedDirectory -Path $results

Push-Location $root
try {
    Invoke-ProofCommand "Pull pinned Freqtrade image" {
        docker compose pull freqtrade-backtest
    }
    Invoke-ProofCommand "Build pinned VectorBT image" {
        docker compose build --pull vectorbt-proof
    }
    Invoke-ProofCommand "Prepare shared Freqtrade fixture" {
        docker compose run --rm freqtrade-prepare
    }

    foreach ($runId in @("run1", "run2")) {
        $env:PHASE1_RUN_ID = $runId
        Reset-GeneratedDirectory -Path $results

        Invoke-ProofCommand "VectorBT proof $runId" {
            docker compose run --rm vectorbt-proof
        }
        Invoke-ProofCommand "Freqtrade proof $runId" {
            docker compose run --rm freqtrade-backtest
        }
        Assert-BacktestResult
        Invoke-ProofCommand "Verify Freqtrade proof $runId" {
            docker compose run --rm freqtrade-verify
        }
    }

    Invoke-ProofCommand "Compare engines and repeated runs" {
        docker compose run --rm compare-results
    }

    $freqtradeImage = docker image inspect "freqtradeorg/freqtrade:2026.6" |
        ConvertFrom-Json
    $vectorbtImage = docker image inspect "strategy-lab-vectorbt:0.28.5-phase1" |
        ConvertFrom-Json
    $manifest = [ordered]@{
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        docker_server = (docker version --format "{{json .Server}}" | ConvertFrom-Json)
        freqtrade = [ordered]@{
            tag = "freqtradeorg/freqtrade:2026.6"
            image_id = $freqtradeImage.Id
            repo_digests = @($freqtradeImage.RepoDigests)
        }
        vectorbt = [ordered]@{
            tag = "strategy-lab-vectorbt:0.28.5-phase1"
            image_id = $vectorbtImage.Id
            repo_digests = @($vectorbtImage.RepoDigests)
        }
    }
    $manifest |
        ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $artifacts "environment-manifest.json") -Encoding utf8

    Write-Host "`nPhase 1 proof execution passed." -ForegroundColor Green
    Write-Host "Artifacts: $artifacts"
}
finally {
    Remove-Item Env:\PHASE1_RUN_ID -ErrorAction SilentlyContinue
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose down --remove-orphans *> $null
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}
