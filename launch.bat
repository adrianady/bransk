@echo off
setlocal enabledelayedexpansion
title ChronoKG Launcher
cd /d "C:\bransk\chronokg"
echo ========================================
echo    ChronoKG - Chronological Knowledge Graph
echo ========================================
echo.

:: Check prerequisites
where python >nul 2>&1 || ( echo [ERROR] Python not found.& pause & exit /b 1 )
where node >nul 2>&1 || ( echo [ERROR] Node.js not found.& pause & exit /b 1 )
echo [OK] Prerequisites found.

:: Install backend deps
echo [1/4] Installing backend deps...
cd /d "C:\bransk\chronokg\backend"
pip install -r requirements.txt 2>nul
cd /d "C:\bransk\chronokg"

:: Start backend
echo [2/4] Starting backend...
netstat -ano 2>nul | findstr /C:":8000 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo   Backend already running on port 8000.
) else (
    start "ChronoKG Backend" /D "C:\bransk\chronokg\backend" cmd /k "set PYTHONPATH=C:\bransk\chronokg\backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
    timeout /t 5 /nobreak >nul
)

:: Install frontend deps
echo [3/4] Installing frontend deps...
cd /d "C:\bransk\chronokg\frontend"
call npm install 2>nul
cd /d "C:\bransk\chronokg"

:: Start frontend
echo [4/4] Starting frontend...
netstat -ano 2>nul | findstr /C:":5173 " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo   Frontend already running on port 5173.
) else (
    start "ChronoKG Frontend" /D "C:\bransk\chronokg\frontend" cmd /k "npm run dev"
    timeout /t 5 /nobreak >nul
)

:: Open browser
start http://localhost:5173

echo.
echo ========================================
echo    ChronoKG is starting!
echo    Frontend: http://localhost:5173
echo    Backend:  http://localhost:8000
echo ========================================
echo.
echo Close this window to keep servers running.
pause >nul

:: Kill background processes
taskkill /f /t /fi "WINDOWTITLE eq ChronoKG Backend" >nul 2>&1
taskkill /f /t /fi "WINDOWTITLE eq ChronoKG Frontend" >nul 2>&1
echo Servers stopped.
