[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$TaskName = "TradeMind AI - Dry Run"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $Task) {
    Write-Host "Scheduled task is not registered: $TaskName"
    exit 0
}

if ($Task.State -eq "Running") {
    Stop-ScheduledTask -InputObject $Task

    $StopDeadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } while (
        $null -ne $Task -and
        $Task.State -eq "Running" -and
        (Get-Date) -lt $StopDeadline
    )

    if ($null -ne $Task -and $Task.State -eq "Running") {
        throw "Scheduled task did not stop within 10 seconds: $TaskName"
    }
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Task) {
    Write-Host "Scheduled task stopped and is no longer registered: $TaskName"
    exit 0
}

Unregister-ScheduledTask -InputObject $Task -Confirm:$false
Write-Host "Removed scheduled task: $TaskName"