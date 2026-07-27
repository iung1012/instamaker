param(
    [string]$MorningTime = "09:00",
    [string]$EveningTime = "21:00",
    [string]$TaskPrefix = "InstagramAutomation",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

function Test-TimeFormat {
    param([string]$Value)
    return $Value -match "^(?:[01]\d|2[0-3]):[0-5]\d$"
}

if (-not (Test-TimeFormat $MorningTime)) {
    throw "Horario invalido para MorningTime: $MorningTime (use HH:mm)."
}
if (-not (Test-TimeFormat $EveningTime)) {
    throw "Horario invalido para EveningTime: $EveningTime (use HH:mm)."
}

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runnerPath = Join-Path $projectDir "run_automation.ps1"

if (-not (Test-Path -LiteralPath $runnerPath)) {
    throw "Arquivo nao encontrado: $runnerPath"
}

$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runnerPath`""
$times = @($MorningTime, $EveningTime)
$hasScheduledTaskCmdlets = $null -ne (Get-Command Set-ScheduledTask -ErrorAction SilentlyContinue)

foreach ($time in $times) {
    $timeToken = $time.Replace(":", "")
    $taskName = "$TaskPrefix-$timeToken"

    $createArgs = @(
        "/Create",
        "/SC", "DAILY",
        "/TN", $taskName,
        "/TR", $taskCommand,
        "/ST", $time,
        "/F"
    )

    & schtasks.exe @createArgs | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao criar tarefa: $taskName"
    }

    if ($hasScheduledTaskCmdlets) {
        $taskSettings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -MultipleInstances IgnoreNew
        Set-ScheduledTask -TaskName $taskName -Settings $taskSettings | Out-Null
    }

    Write-Host "Tarefa configurada: $taskName ($time)"

    if ($RunNow) {
        & schtasks.exe /Run /TN $taskName | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao iniciar tarefa: $taskName"
        }
        Write-Host "Tarefa iniciada manualmente: $taskName"
    }
}

Write-Host "\nResumo das tarefas:"
foreach ($time in $times) {
    $timeToken = $time.Replace(":", "")
    $taskName = "$TaskPrefix-$timeToken"
    & schtasks.exe /Query /TN $taskName /FO LIST /V | Out-Host
}
