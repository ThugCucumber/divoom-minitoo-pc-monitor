param([string]$TaskName = 'MiniToo PC Monitor')
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host 'Task does not exist.'; exit 0 }
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedScript = Join-Path $projectRoot 'monitor_supervisor.py'
if (-not ($task.Actions | Where-Object { $_.Arguments -eq ('"' + $expectedScript + '"') })) {
    throw 'Task does not point to this project copy; refusing to remove it.'
}
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host 'Autostart removed. An already running worker may finish separately; see README.md.'
