# Shared by install-backend-task.ps1 and uninstall-backend-task.ps1.

# Stops the LLM Judge backend and the run-backend-service.bat loop that keeps it
# alive. Only processes whose command line names them are touched.
function Stop-LlmJudgeBackend {
    $mine = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -like "*run-backend-service.bat*" -or
            $_.CommandLine -like "*backend.dashboard.app*"
        )
    }
    foreach ($p in $mine) {
        Write-Host "Stopping process $($p.ProcessId): $($p.Name)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
