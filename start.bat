@echo off
echo Starting NEXUS + Rakshak...
echo.

REM Start Rakshak (CCTV ML) — port 8001
echo [1/3] Starting Rakshak (YOLOv8 CCTV detection)...
start "Rakshak CCTV" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn rakshak.main:app --port 8001 --host 0.0.0.0"

timeout /t 5 /nobreak >nul

REM Start NEXUS backend — port 8000
echo [2/3] Starting NEXUS backend (FastAPI + WebSocket)...
start "NEXUS Backend" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000"

timeout /t 4 /nobreak >nul

REM Start frontend
echo [3/3] Starting frontend (React)...
start "NEXUS Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"

echo.
echo All services started:
echo.
echo   Rakshak:  http://localhost:8001  (CCTV YOLOv8 inference)
echo   NEXUS:    http://localhost:8000  (AI City Governor)
echo   Frontend: http://localhost:3000  (Live Dashboard)
echo.
echo   API docs: http://localhost:8000/docs
echo             http://localhost:8001/docs
echo.
echo Upload a video at http://localhost:8001 to start YOLOv8 inference.
echo.
