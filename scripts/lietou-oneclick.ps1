[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$DoctorOnly,
    [switch]$SkipDocker,
    [switch]$NoInit,
    [switch]$NoBuild,
    [string]$RepoRoot,
    [string]$Python
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)

    Write-Host ""
    Write-Host "==> $Message"
}

function Stop-WithExit {
    param(
        [string]$Message,
        [int]$Code = 1
    )

    Write-Host $Message -ForegroundColor Red
    exit $Code
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
    if ($exitCode -ne 0) {
        Stop-WithExit -Message $FailureMessage -Code $exitCode
    }
}

if ($Start -and $DoctorOnly) {
    Stop-WithExit -Message "Use either -Start or -DoctorOnly, not both." -Code 2
}
if ($Start -and $SkipDocker) {
    Stop-WithExit -Message "Cannot use -SkipDocker with -Start because Docker Compose is required to start the stack." -Code 2
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($RepoRoot) {
    $resolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
} else {
    $resolvedRoot = (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path
}

Set-Location -LiteralPath $resolvedRoot

if (-not $Python) {
    $venvPython = Join-Path $resolvedRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $Python = $venvPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            Stop-WithExit -Message "Python was not found. Create .venv or pass -Python C:\path\to\python.exe." -Code 127
        }
        $Python = $pythonCommand.Source
    }
}

Write-Step -Message "Running local deployment doctor"
$doctorArgs = @("-m", "app.runtime.local_doctor", "--repo-root", $resolvedRoot)
if (-not $NoInit) {
    $doctorArgs += @("--init-env", "--generate-local-secrets")
}
if ($SkipDocker) {
    $doctorArgs += "--skip-docker"
}
Invoke-Checked -FilePath $Python -Arguments $doctorArgs -FailureMessage "Local doctor failed."

if ($DoctorOnly -or -not $Start) {
    Write-Host ""
    Write-Host "Preflight finished. Fill .env, then run: powershell -ExecutionPolicy Bypass -File scripts\lietou-oneclick.ps1 -Start"
    exit 0
}

Write-Step -Message "Verifying strict readiness before Docker start"
$strictArgs = @("-m", "app.runtime.local_doctor", "--repo-root", $resolvedRoot, "--strict")
Invoke-Checked -FilePath $Python -Arguments $strictArgs -FailureMessage "Strict local readiness check failed. Fill .env and fix Docker before starting."

Write-Step -Message "Starting Docker Compose stack"
$composeArgs = @("compose", "up", "-d")
if (-not $NoBuild) {
    $composeArgs += "--build"
}
Invoke-Checked -FilePath "docker" -Arguments $composeArgs -FailureMessage "Docker Compose start failed."

Write-Step -Message "Current Docker Compose services"
& docker compose ps
exit $LASTEXITCODE
