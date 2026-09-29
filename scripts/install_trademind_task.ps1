[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$TaskName = "TradeMind AI - Dry Run"
$ProjectDirectory = "D:\trade\trademind-ai"
$PythonExecutable = "D:\trade\trademind-ai\.venv310\Scripts\python.exe"
$LogDirectory = Join-Path $ProjectDirectory "logs"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $ProjectDirectory -PathType Container)) {
    throw "Required project directory does not exist: $ProjectDirectory"
}

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Required Python executable does not exist: $PythonExecutable"
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

[System.Environment]::SetEnvironmentVariable("TRADING_MODE", "paper", "User")
[System.Environment]::SetEnvironmentVariable("LIVE_TRADING_ENABLED", "false", "User")
[System.Environment]::SetEnvironmentVariable("PYTHONUNBUFFERED", "1", "User")

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $ExistingTask) {
    Stop-ScheduledTask -InputObject $ExistingTask -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -InputObject $ExistingTask -Confirm:$false
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonExecutable `
    -Argument "-u -m backend.app.trading.runner" `
    -WorkingDirectory $ProjectDirectory

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser

$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Starts TradeMind AI for the logged-in user in paper-mode dry run."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
Write-Host "User: $CurrentUser"
Write-Host "Working directory: $ProjectDirectory"
Write-Host "Python: $PythonExecutable"
Write-Host "Module: backend.app.trading.runner"
Write-Host "Log directory: $LogDirectory"
Write-Host "Safety environment: TRADING_MODE=paper; LIVE_TRADING_ENABLED=false; PYTHONUNBUFFERED=1"