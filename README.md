# Rakshak — AI-Powered CCTV City Intelligence

Real-time incident detection on CCTV footage for Hyderabad. Rakshak runs YOLOv8 over camera feeds, flags accidents, flooding and other incidents, maps them live on a city dashboard, and hands them to an AI "city governor" (NEXUS) that decides who to alert.

🏆 **Top 10 of 100+ teams — Ignite, BITS Goa (2025)**

YOLOv8 · OpenCV · FastAPI · React · Gemini · Twilio

## How it works

1. **Rakshak CCTV server** (port 8001) takes uploaded or streamed video, runs YOLOv8 frame by frame and classifies incidents.
2. **NEXUS backend** (port 8000) receives detections, keeps a digital twin of the city, and uses Gemini (or a rules-based fallback) to decide on a response.
3. **Alerts** go out as Twilio calls and SMS to the relevant department.
4. **Dashboard** (port 3000) shows cameras, incidents on a live map, and the agent's reasoning for each decision.

## Requirements

- Python 3.10+
- Node.js 18+

## Setup

**1. Environment variables.** Copy the examples and fill them in:

```bash
cp backend/.env.example backend/.env.local
cp frontend/.env.example frontend/.env.local
```

- `NEXUS_API_KEY` is a shared secret you choose. Use any long random string, and set the same value as `VITE_API_KEY` in `frontend/.env.local`.
- `GEMINI_API_KEY` and the `TWILIO_*` keys are optional. Without them the system runs in rules-based fallback mode, with no LLM and no calls.

**2. Start everything.**

On Windows, double-click `start.bat`. It creates a virtual environment, installs dependencies, and opens one terminal per service.

On macOS or Linux, run each service in its own terminal:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt -r rakshak/requirements.txt
(cd frontend && npm install)

uvicorn backend.api.server:app --port 8000 --reload
uvicorn rakshak.api.server:app --port 8001 --reload
cd frontend && npm run dev
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| Rakshak CCTV | http://localhost:8001 |
| NEXUS API docs | http://localhost:8000/docs |

## Using the demo

1. Open http://localhost:8001 (Rakshak CCTV).
2. Pick a camera slot and upload a clip from `test_videos/`.
3. Watch detections appear on the city map at http://localhost:3000.
4. Click **Analyse incident** on a detection card to see the agent reason through it.
5. Click **✓ Resolved** to dismiss it.

## Project structure

```
backend/          NEXUS — FastAPI city governor (port 8000)
  api/            REST + WebSocket endpoints
  governor/       AI decision engine (Gemini / rules fallback)
  simulation/     city digital twin
  alerts/         Twilio call/SMS dispatcher
rakshak/          CCTV inference server (port 8001)
  api/            upload, stream and frame endpoints
  core/           YOLOv8 video processor + classifier
  models/         yolov8n.pt (downloaded on first run)
frontend/         React dashboard (port 3000)
security.py       shared API-key auth, rate limiting, upload validation
test_videos/      sample incident clips for the demo
start.bat         one-click launcher (Windows)
```
