@echo off
title Solar Atlas - Live Public Deployment
cd /d "%~dp0.."

echo ======================================================================
echo   SOLAR ATLAS PRO - AUTOMATED LIVE PUBLIC DEPLOYMENT
echo ======================================================================

:: 1. Ensure virtual environment exists
if not exist ".venv\Scripts\python.exe" (
    echo [!] Virtual environment not found. Creating .venv...
    py -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

:: 2. Check or download cloudflared
if not exist "%TEMP%\cloudflared.exe" (
    echo [+] Downloading Cloudflare Tunnel runner...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '$env:TEMP\cloudflared.exe' -UseBasicParsing"
)

:: 3. Launch Streamlit server in the background
echo [+] Starting Solar Atlas Streamlit server on http://localhost:8501...
start "Solar Atlas Server" /min .venv\Scripts\python.exe -m streamlit run frontend\app.py --server.port 8501 --server.headless true

:: 4. Launch public HTTPS tunnel
echo [+] Creating secure public HTTPS URL...
timeout /t 3 /nobreak >nul
"%TEMP%\cloudflared.exe" tunnel --url http://localhost:8501

pause
