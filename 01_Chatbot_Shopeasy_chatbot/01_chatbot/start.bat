@echo off
REM One-click start: backend (FastAPI:8202) + frontend (Vite:5173)
cd /d "%~dp0"

if not exist ".env" (
    echo [WARN] .env missing. Copy .env.sample to .env and add your GROQ_API_KEY first.
    pause
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"

"..\..\.venv\Scripts\python.exe" -c "import groq" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing chatbot backend dependency...
    "..\..\.venv\Scripts\python.exe" -m pip install groq==1.7.0
    if errorlevel 1 (
        echo [ERROR] Backend dependency installation failed.
        pause
        exit /b 1
    )
)

if not exist "frontend\node_modules\vite\bin\vite.js" (
    echo [INFO] Installing frontend dependencies...
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        echo [ERROR] Frontend dependency installation failed.
        pause
        exit /b 1
    )
    popd
)

start "Backend (8202)" cmd /k "cd backend && set "GROQ_API_KEY=%GROQ_API_KEY%" && set "CHATBOT_MODEL=%CHATBOT_MODEL%" && ..\..\..\.venv\Scripts\python -m uvicorn app:app --reload --port 8202"
start "Frontend (5173)" cmd /k "cd frontend && npm run dev"

timeout /t 3 >nul
start "" "http://localhost:5173"
