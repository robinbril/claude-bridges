# install-autostart.ps1 — registreert stack-ensure.ps1 als logon-task.
# Eenmalig draaien (als eigen gebruiker, geen admin nodig). Daarna brengt
# elke logon de volledige bridge-stack idempotent up. Herdraaien overschrijft
# de bestaande task.
$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'stack-ensure.ps1'
if (-not (Test-Path $script)) { throw "stack-ensure.ps1 niet gevonden naast dit script" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName 'Bridge-Stack-Autostart' -Action $action `
  -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Task 'Bridge-Stack-Autostart' geregistreerd (logon, $script)"
