@echo off
title ChronoKG
cd /d "C:\bransk\chronokg"
echo ========================================
echo    ChronoKG - Chronological Knowledge Graph
echo ========================================
echo.

:: Start backend (check if already running)
echo [1/3] Starting backend...
tasklist /fi "WindowTitle eq ChronoKG Backend*" 2>nul | find /i "python" >nul
if %errorlevel% equ 0 (
    echo   Backend already running.
) else (
    start "ChronoKG Backend" cmd /c "cd /d C:\bransk\chronokg\backend && set PYTHONPATH=C:\bransk\chronokg\backend && set OLLAMA_BASE_URL=http://localhost:11434 && set OLLAMA_MODEL=qwen2.5:3b && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
)

:: Wait for backend to start
echo   Waiting for backend...
:waitloop
timeout /t 2 /nobreak >nul
curl -s http://localhost:8000/api/health >nul 2>&1
if %errorlevel% neq 0 goto waitloop
echo   Backend is ready!

:: Install frontend deps if needed
echo [2/3] Checking frontend dependencies...
if not exist "frontend\node_modules" (
    echo   Installing dependencies (first time)...
    cd frontend
    call npm install
    cd ..
)

:: Start frontend
echo [3/3] Starting frontend...
start "ChronoKG Frontend" cmd /c "cd /d C:\bransk\chronokg\frontend && npm run dev"

:: Open browser
timeout /t 3 /nobreak >nul
start http://localhost:5173

echo.
echo ========================================
echo    ChronoKG is running!
echo    Frontend: http://localhost:5173
echo    Backend:  http://localhost:8000
echo    Press any key to exit (servers keep running)
echo ========================================
pause >nul
