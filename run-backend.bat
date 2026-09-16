@echo off
rem Starts the LLM Judge backend on http://127.0.0.1:8000 (the extension expects this address).
rem Runs in the project's own environment (.venv), which is created on the first run.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating the project environment in .venv - first run only, this takes a few minutes...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
set DEEPEVAL_TELEMETRY_OPT_OUT=1
".venv\Scripts\python.exe" -m uvicorn backend.dashboard.app:app --host 127.0.0.1 --port 8000
goto :eof

:error
echo Setup failed. See the messages above.
exit /b 1
