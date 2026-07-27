param(
    [string]$PythonBin = "",
    [ValidateSet("reels", "carousel")]
    [string]$Mode = "reels",
    [switch]$PublishYouTubeShorts,
    [switch]$SkipInstagram,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

$logsDir = Join-Path $projectDir "logs"
if (-not (Test-Path -LiteralPath $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

$lockFile = Join-Path $projectDir ".automation.lock"
if (Test-Path -LiteralPath $lockFile) {
    Write-Host "Pipeline ja esta em execucao. Ignorando esta execucao."
    exit 0
}

New-Item -ItemType File -Path $lockFile | Out-Null

try {
    if ($PythonBin) {
        $python = $PythonBin
    }
    else {
        $venvPython = Join-Path $projectDir "venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $venvPython) {
            $python = $venvPython
        }
        else {
            $python = "python"
        }
    }

    $timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
    $logFile = Join-Path $logsDir "automation_$timestamp.log"

    $runnerArgs = @("automation_pipeline.py", "--mode", $Mode)
    if ($PublishYouTubeShorts) {
        $runnerArgs += "--publish-youtube-shorts"
    }
    if ($SkipInstagram) {
        $runnerArgs += "--skip-instagram"
    }
    if ($DryRun) {
        $runnerArgs += "--dry-run"
    }

    & $python @runnerArgs *>&1 | Tee-Object -FilePath $logFile
    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $lockFile -ErrorAction SilentlyContinue
}
