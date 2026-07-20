param(
    [switch]$ResumeAfterVerification
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$artifacts = [System.IO.Path]::GetFullPath((Join-Path $root "artifacts"))
$compiled = [System.IO.Path]::GetFullPath((Join-Path $root "compiled"))
$runtimeUserData = [System.IO.Path]::GetFullPath(
    (Join-Path $root "runtime\user_data")
)
$data = [System.IO.Path]::GetFullPath(
    (Join-Path $runtimeUserData "data")
)
$results = [System.IO.Path]::GetFullPath(
    (Join-Path $runtimeUserData "backtest_results")
)
$config = [System.IO.Path]::GetFullPath(
    (Join-Path $runtimeUserData "config.json")
)
$sourceStrategy = [System.IO.Path]::GetFullPath(
    (Join-Path $root "freqtrade\user_data\strategies\ApprovedEma9FixedHoldStrategy.py")
)
$runtimeStrategy = [System.IO.Path]::GetFullPath(
    (Join-Path $runtimeUserData "strategies\ApprovedEma9FixedHoldStrategy.py")
)
$probeName = "strategy-lab-phase4-cancellation-probe"

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
        throw "Refusing filesystem operation outside Phase 4 root: $Child"
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
        throw "Freqtrade did not create .last_result.json. Review its output above."
    }
}

function Remove-ProbeContainer {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker rm --force $probeName 2>&1 | Out-Null
    $ErrorActionPreference = $previousErrorActionPreference
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is unavailable. Install or repair Docker Desktop first."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Start Docker Desktop and wait until the engine reports that it is running."
}

Add-Type -AssemblyName Microsoft.VisualBasic
$computerInfo = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
$availableGb = [math]::Round($computerInfo.AvailablePhysicalMemory / 1GB, 1)
if ($availableGb -lt 0.8) {
    throw "Only $availableGb GB RAM is available. Close applications until at least 0.8 GB is free."
}
if ($availableGb -lt 1.25) {
    Write-Warning "Only $availableGb GB RAM is free. The proof may run slowly, but it can continue."
}

if ($ResumeAfterVerification) {
    $verifiedResult = Join-Path $artifacts "verified-result.json"
    if (-not (Test-Path -LiteralPath $verifiedResult -PathType Leaf)) {
        throw "Cannot resume because artifacts\verified-result.json is missing."
    }
    $verifiedPayload = Get-Content -LiteralPath $verifiedResult -Raw |
        ConvertFrom-Json
    if ($verifiedPayload.status -ne "passed") {
        throw "Cannot resume because the saved verification status is not passed."
    }
}
else {
    foreach ($path in @($artifacts, $compiled, $runtimeUserData)) {
        Reset-GeneratedDirectory -Path $path
    }
    Assert-ChildPath -Parent $root -Child $config
}

Push-Location $root
try {
    if ($ResumeAfterVerification) {
        Write-Host "`n== Resume after passed lifecycle verification ==" -ForegroundColor Cyan
    }
    else {
        Invoke-ProofCommand "Validate isolated adapter" {
            docker compose run --rm adapter-tests
        }
        Invoke-ProofCommand "Compile reviewed contract" {
            docker compose run --rm compile-adapter
        }
        New-Item -ItemType Directory -Path (Split-Path $runtimeStrategy) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $compiled "config.json") -Destination $config
        Copy-Item -LiteralPath $sourceStrategy -Destination $runtimeStrategy
        Invoke-ProofCommand "Prepare deterministic futures fixture" {
            docker compose run --rm freqtrade-prepare
        }
        Invoke-ProofCommand "Run Freqtrade lifecycle" {
            docker compose run --rm freqtrade-backtest
        }
        Assert-BacktestResult
        Invoke-ProofCommand "Reconcile timestamps and costs" {
            docker compose run --rm freqtrade-verify
        }
    }

    Write-Host "`n== Test isolated worker cancellation ==" -ForegroundColor Cyan
    Remove-ProbeContainer
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $probeOutput = docker compose run --detach --name $probeName cancellation-probe 2>&1
    $probeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($probeExitCode -ne 0) {
        throw "Cancellation probe failed to start.`n$($probeOutput -join [Environment]::NewLine)"
    }
    $runningProbe = @(docker inspect $probeName | ConvertFrom-Json)[0]
    if (-not $runningProbe.State.Running) {
        throw "Cancellation probe did not enter the running state."
    }
    if (
        -not $runningProbe.HostConfig.ReadonlyRootfs -or
        $runningProbe.HostConfig.NetworkMode -ne "none" -or
        [int64]$runningProbe.HostConfig.Memory -ne 768MB -or
        [int64]$runningProbe.HostConfig.NanoCpus -ne 1000000000 -or
        [int64]$runningProbe.HostConfig.PidsLimit -ne 128 -or
        "no-new-privileges:true" -notin @($runningProbe.HostConfig.SecurityOpt)
    ) {
        throw "Cancellation probe did not receive the required isolation controls."
    }
    docker stop --time 5 $probeName *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Cancellation probe did not stop cleanly."
    }
    $stoppedProbe = @(docker inspect $probeName | ConvertFrom-Json)[0]
    if ($stoppedProbe.State.Running) {
        throw "Cancellation probe is still running after stop."
    }
    [ordered]@{
        status = "passed"
        tested_at_utc = [DateTime]::UtcNow.ToString("o")
        container_name = $probeName
        running_before_cancel = $true
        running_after_cancel = $false
        network_mode = $runningProbe.HostConfig.NetworkMode
        read_only_root = [bool]$runningProbe.HostConfig.ReadonlyRootfs
        memory_bytes = [int64]$runningProbe.HostConfig.Memory
        nano_cpus = [int64]$runningProbe.HostConfig.NanoCpus
        pids_limit = [int64]$runningProbe.HostConfig.PidsLimit
        security_options = @($runningProbe.HostConfig.SecurityOpt)
    } |
        ConvertTo-Json -Depth 6 |
        Set-Content -LiteralPath (Join-Path $artifacts "cancellation-proof.json") -Encoding utf8
    docker rm $probeName *> $null

    $image = docker image inspect "freqtradeorg/freqtrade:2026.6" |
        ConvertFrom-Json
    [ordered]@{
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        docker_server = (docker version --format "{{json .Server}}" | ConvertFrom-Json)
        freqtrade = [ordered]@{
            tag = "freqtradeorg/freqtrade:2026.6"
            image_id = $image.Id
            repo_digests = @($image.RepoDigests)
        }
        resource_limit = [ordered]@{
            cpus = 1.0
            memory_bytes = 768MB
            pids = 128
        }
    } |
        ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $artifacts "environment-manifest.json") -Encoding utf8

    docker compose config --format json |
        Set-Content -LiteralPath (Join-Path $artifacts "resolved-compose.json") -Encoding utf8

    Write-Host "`nPhase 4 runtime proof passed." -ForegroundColor Green
    Write-Host "Artifacts: $artifacts"
}
finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Remove-ProbeContainer
    docker compose down --remove-orphans 2>&1 | Out-Null
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
}
