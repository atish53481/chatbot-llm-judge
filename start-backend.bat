@echo off
echo Installing Flask backend dependencies...
pip install flask flask-cors

echo.
echo Starting DeepEval backend server...
echo Backend will run at http://127.0.0.1:5000
echo.
echo Press Ctrl+C to stop the server
echo.

cd /d "%~dp0"
python backend.py
