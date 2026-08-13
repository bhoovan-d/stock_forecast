# Register the daily run as a Windows scheduled task.
#
# Run this once, from an elevated PowerShell prompt:
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
#
# Default is 18:45 local time on weekdays. NSE closes 15:30 IST and the bhavcopy lands
# around 18:00 IST, so this leaves headroom for a late publish.

param(
    [string]$Time = "18:45",
    [string]$TaskName = "AsymmetryDailyBrief"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $ProjectRoot "scripts\daily_run.ps1"

if (-not (Test-Path $Script)) { throw "Cannot find $Script" }

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $ProjectRoot

# Weekdays only — the market is shut at weekends and the archive would 404.
$Trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Time

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "Asymmetry Engine — post-close brief" -Force | Out-Null

Write-Output "Registered '$TaskName' for $Time on weekdays."
Write-Output ""
Write-Output "Run now:      Start-ScheduledTask -TaskName $TaskName"
Write-Output "Check status: Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Output "Remove:       Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Output ""
Write-Output "Logs: data\logs\run_<date>.log"
