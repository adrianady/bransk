@echo off
echo ========================================
echo    ChronoKG - Chronological Knowledge Graph
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed. Install Python 3.11+ and try again.
    pause
    exit /b 1
)

:: Check Node.js
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed. Install Node 18+ and try again.
    pause
    exit /b 1
)

:: Install backend dependencies
echo [1/4] Installing backend dependencies...
cd backend
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install backend dependencies.
    pause
    exit /b 1
)

:: Start backend
echo [2/4] Starting backend server on http://localhost:8000...
start "ChronoKG Backend" cmd /c "uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
cd ..

:: Wait for backend to start
timeout /t 3 /nobreak >nul

:: Install frontend dependencies
echo [3/4] Installing frontend dependencies...
cd frontend
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install frontend dependencies.
    pause
    exit /b 1
)

:: Start frontend
echo [4/4] Starting frontend on http://localhost:5173...
start "ChronoKG Frontend" cmd /c "npm run dev"
cd ..

echo.
echo ========================================
echo    ChronoKG is starting!
echo    Frontend: http://localhost:5173
echo    Backend:  http://localhost:8000
echo    API Docs: http://localhost:8000/docs
echo ========================================
echo.
echo Press any key to stop all servers...
pause >nul

:: Kill background processes
taskkill /f /fi "WINDOWTITLE eq ChronoKG Backend" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq ChronoKG Frontend" >nul 2>&1

echo Servers stopped.
