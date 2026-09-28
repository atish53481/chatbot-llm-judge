#Requires -RunAsAdministrator
<#
Removes the "LLM Judge Backend" scheduled task and stops the backend it runs.
Afterwards start the backend with run-backend.bat, as before.

Run in PowerShell opened with "Run as administrator", from this folder:
    powershell -ExecutionPolicy Bypass -File .\uninstall-backend-task.ps1
#>
param([string]$TaskName = "LLM Judge Backend")
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "backend-task-common.ps1")
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the scheduled task '$TaskName'."
} else {
    Write-Host "No scheduled task named '$TaskName'."
}
Stop-LlmJudgeBackend
Write-Host "The backend is stopped. Start it with run-backend.bat when you need it."
