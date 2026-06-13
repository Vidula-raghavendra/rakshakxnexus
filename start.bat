@echo off
setlocal
cd /d %~dp0

echo.
echo  Rakshak — Startup
echo  ==================
echo.

REM ── 1. Find Python ──────────────────────────────────────────────────────────
set PYTHON=
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
    echo [OK] Using .venv
    goto :check_node
)

where python >nul 2>&1
if %errorlevel%==0 (
    set PYTHON=python
    echo [OK] Using system Python
    goto :check_node
)

where python3 >nul 2>&1
if %errorlevel%==0 (
    set PYTHON=python3
    echo [OK] Using system python3
    goto :check_node
)

echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
pause & exit /b 1

:check_node
REM ── 2. Find Node / npm ──────────────────────────────────────────────────────
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js / npm not found. Install from https://nodejs.org
    pause & exit /b 1
)
echo [OK] npm found

REM ── 3. Install Python deps if not already ───────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [SETUP] Creating virtual environment...
    python -m venv .venv
    set PYTHON=.venv\Scripts\python.exe
)

echo [SETUP] Installing Python dependencies...
.venv\Scripts\python.exe -m pip install -q --upgrade pip
.venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt
.venv\Scripts\python.exe -m pip install -q -r rakshak\requirements.txt
echo [OK] Python deps ready

REM ── 4. Install frontend deps if not already ─────────────────────────────────
if not exist "frontend\node_modules" (
    echo [SETUP] Installing frontend npm packages...
    cd frontend && npm install --silent && cd ..
    echo [OK] npm packages ready
) else (
    echo [OK] node_modules present
)

REM ── 5. Kill anything on ports 8000 / 8001 ───────────────────────────────────
echo.
echo [START] Clearing ports...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8001 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM ── 6. Start all three services ──────────────────────────────────────────────
echo [START] NEXUS backend  (port 8000)...
start "NEXUS Backend" cmd /k ".venv\Scripts\python.exe -m uvicorn backend.api.server:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 3 /nobreak >nul

echo [START] Rakshak CCTV   (port 8001)...
start "Rakshak CCTV" cmd /k ".venv\Scripts\python.exe -m uvicorn rakshak.api.server:app --host 0.0.0.0 --port 8001 --reload"

timeout /t 3 /nobreak >nul

echo [START] Frontend        (port 3000)...
start "Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo  =========================================
echo   Dashboard  ->  http://localhost:3000
echo   CCTV feed  ->  http://localhost:8001
echo   API docs   ->  http://localhost:8000/docs
echo  =========================================
echo.
echo  To stop: close the three terminal windows,
echo  or press Ctrl+C in each one.
echo.
