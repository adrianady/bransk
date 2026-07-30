@echo off
setlocal enabledelayedexpansion
title ChronoKG Launcher & Control Suite

:: -------------------------------------------------------------------
:: 1. Script Environment & Path Setup (Script-Relative Resolution)
:: -------------------------------------------------------------------
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

set "BACKEND_DIR=%ROOT_DIR%\backend"
set "FRONTEND_DIR=%ROOT_DIR%\frontend"

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

set "VENV_PYTHON=python"
if exist "%BACKEND_DIR%\venv\Scripts\python.exe" (
    set "VENV_PYTHON=%BACKEND_DIR%\venv\Scripts\python.exe"
)

echo ===================================================================
echo    ChronoKG - Chronological Knowledge Graph Production Launcher
echo ===================================================================
echo Workspace Root: "%ROOT_DIR%"
echo.

:: -------------------------------------------------------------------
:: 2. Check Shutdown Argument
:: -------------------------------------------------------------------
if /i "%~1"=="stop" goto stop_servers

:: -------------------------------------------------------------------
:: 3. Prerequisite CLI Tool Verification
:: -------------------------------------------------------------------
echo [1/5] Verifying System Prerequisites...

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.11+ is not found in system PATH.
    echo Please install Python and ensure it is added to your PATH.
    pause
    exit /b 1
)

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not found in system PATH.
    pause
    exit /b 1
)

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm is not found in system PATH.
    pause
    exit /b 1
)

where curl >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] curl is not found in system PATH.
    pause
    exit /b 1
)

echo   [OK] All prerequisite tools detected.

:: -------------------------------------------------------------------
:: 4. Backend Process & Port Verification
:: -------------------------------------------------------------------
echo.
echo [2/5] Checking Backend Status on Port %BACKEND_PORT%...

set BACKEND_RUNNING=0
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /C:":%BACKEND_PORT% " ^| findstr "LISTENING"') do (
    set BACKEND_PID=%%a
    set BACKEND_RUNNING=1
)

if %BACKEND_RUNNING% equ 1 (
    echo   [INFO] Backend already running on port %BACKEND_PORT% (PID: !BACKEND_PID!).
) else (
    echo   Starting FastAPI Backend Server...
    start "ChronoKG Backend" /D "%BACKEND_DIR%" cmd /k "%VENV_PYTHON% -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"
)

:: -------------------------------------------------------------------
:: 5. Bounded Health Check Wait Loop (30-Second Max Timeout)
:: -------------------------------------------------------------------
echo.
echo [3/5] Awaiting Backend Service Readiness...

set RETRY_COUNT=0
set MAX_RETRIES=15
set BACKEND_READY=0

:health_poll_loop
if !RETRY_COUNT! geq %MAX_RETRIES% (
    echo.
    echo [CRITICAL ERROR] Backend failed to respond within 30 seconds!
    echo Check the "ChronoKG Backend" terminal window for errors.
    pause
    exit /b 1
)

timeout /t 2 /nobreak >nul
curl -s -m 3 http://localhost:%BACKEND_PORT%/api/health 2>nul | findstr /i "status" >nul
if %errorlevel% equ 0 (
    set BACKEND_READY=1
    echo   [OK] Backend health check passed! (Port %BACKEND_PORT%)
) else (
    set /a RETRY_COUNT+=1
    echo   Waiting for backend... (Attempt !RETRY_COUNT!/%MAX_RETRIES%)
    goto health_poll_loop
)

:: -------------------------------------------------------------------
:: 6. Frontend Dependency Verification & Service Start
:: -------------------------------------------------------------------
echo.
echo [4/5] Checking Frontend Service on Port %FRONTEND_PORT%...

if not exist "%FRONTEND_DIR%\node_modules\vite" (
    echo   Installing Frontend Dependencies (node_modules missing)...
    cd /d "%FRONTEND_DIR%"
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    cd /d "%ROOT_DIR%"
)

set FRONTEND_RUNNING=0
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /C:":%FRONTEND_PORT% " ^| findstr "LISTENING"') do (
    set FRONTEND_PID=%%a
    set FRONTEND_RUNNING=1
)

if %FRONTEND_RUNNING% equ 1 (
    echo   [INFO] Frontend already running on port %FRONTEND_PORT% (PID: !FRONTEND_PID!).
) else (
    echo   Starting Vite Frontend Development Server...
    start "ChronoKG Frontend" /D "%FRONTEND_DIR%" cmd /k "npm run dev"
    timeout /t 3 /nobreak >nul
)

:: -------------------------------------------------------------------
:: 7. Launch Browser & Control Handlers
:: -------------------------------------------------------------------
echo.
echo [5/5] Launching ChronoKG Web Interface...
start http://localhost:%FRONTEND_PORT%

echo.
echo ===================================================================
echo    ChronoKG Application Started Successfully!
echo    - Frontend Web UI:  http://localhost:%FRONTEND_PORT%
echo    - Backend API Docs: http://localhost:%BACKEND_PORT%/docs
echo    - Health Check:     http://localhost:%BACKEND_PORT%/api/health
echo ===================================================================
echo    Press [S] and Enter to Stop all servers cleanly.
echo    Press any other key to exit (servers remain running).
echo ===================================================================
set /p USER_CHOICE="Select Option: "

if /i "%USER_CHOICE%"=="S" goto stop_servers
exit /b 0

:: -------------------------------------------------------------------
:: 8. Clean Process Tree Shutdown Routine
:: -------------------------------------------------------------------
:stop_servers
echo.
echo Terminating ChronoKG Process Trees...

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /C:":%BACKEND_PORT% " ^| findstr "LISTENING"') do (
    set "PORT_PID=%%a"
    set "SAFE_KILL=0"
    for /f "tokens=1" %%i in ('tasklist /FI "PID eq !PORT_PID!" /NH 2^>nul') do (
        if /i "%%i"=="python.exe" set SAFE_KILL=1
        if /i "%%i"=="node.exe" set SAFE_KILL=1
        if /i "%%i"=="cmd.exe" set SAFE_KILL=1
    )
    if !SAFE_KILL! equ 1 (
        echo Killing Backend Process Tree (PID !PORT_PID!)...
        taskkill /F /T /PID !PORT_PID! >nul 2>&1
    ) else (
        echo [WARNING] PID !PORT_PID! is not python/node/cmd. Skipping.
    )
)

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /C:":%FRONTEND_PORT% " ^| findstr "LISTENING"') do (
    set "PORT_PID=%%a"
    set "SAFE_KILL=0"
    for /f "tokens=1" %%i in ('tasklist /FI "PID eq !PORT_PID!" /NH 2^>nul') do (
        if /i "%%i"=="python.exe" set SAFE_KILL=1
        if /i "%%i"=="node.exe" set SAFE_KILL=1
        if /i "%%i"=="cmd.exe" set SAFE_KILL=1
    )
    if !SAFE_KILL! equ 1 (
        echo Killing Frontend Process Tree (PID !PORT_PID!)...
        taskkill /F /T /PID !PORT_PID! >nul 2>&1
    ) else (
        echo [WARNING] PID !PORT_PID! is not python/node/cmd. Skipping.
    )
)

echo [OK] ChronoKG services terminated cleanly.
pause
exit /b 0
