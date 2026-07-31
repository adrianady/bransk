@echo off
setlocal enabledelayedexpansion
title ChronoKG

set "ROOT_DIR=C:\bransk\chronokg"
cd /d "%ROOT_DIR%"

echo ========================================
echo    ChronoKG - Chronological Knowledge Graph
echo ========================================
echo.

:: Check prerequisites
where python >nul 2>&1 || ( echo [ERROR] Python not found.& pause & exit /b 1 )
where node >nul 2>&1 || ( echo [ERROR] Node.js not found.& pause & exit /b 1 )
echo [OK] Prerequisites found.
echo.

:: Step 1: Start backend if not running
echo [1/3] Backend...
netstat -ano 2>nul | findstr /C:":8000 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo   Backend already running on port 8000.
    goto start_frontend
)

echo   Starting backend...
start "ChronoKG Backend" /D "%ROOT_DIR%\backend" cmd /k "set PYTHONPATH=%ROOT_DIR%\backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo   Waiting for backend to become ready...
set RETRIES=0
:wait_loop
timeout /t 2 /nobreak >nul
curl -s -m 2 http://localhost:8000/api/health >nul 2>&1
if !errorlevel! equ 0 (
    echo   Backend ready!
    goto start_frontend
)
set /a RETRIES+=1
if !RETRIES! lss 15 (
    echo   Attempt !RETRIES!/15...
    goto wait_loop
)
echo   Backend failed to start - check the ChronoKG Backend window for errors.
pause
exit /b 1

:: Step 2: Start frontend if not running
:start_frontend
echo.
echo [2/3] Frontend...
if not exist "%ROOT_DIR%\frontend\node_modules\vite" (
    echo   Installing frontend dependencies...
    cd /d "%ROOT_DIR%\frontend"
    call npm install
    cd /d "%ROOT_DIR%"
)
netstat -ano 2>nul | findstr /C:":5173 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo   Frontend already running on port 5173.
) else (
    echo   Starting frontend...
    start "ChronoKG Frontend" /D "%ROOT_DIR%\frontend" cmd /k "npm run dev"
    timeout /t 5 /nobreak >nul
)

:: Step 3: Open browser
echo.
echo [3/3] Opening browser...
start http://localhost:5173

echo.
echo ========================================
echo    ChronoKG is running!
echo    Frontend: http://localhost:5173
echo    Backend:  http://localhost:8000
echo ========================================
echo.
echo Close this window to keep servers running in background.
pause >nul
