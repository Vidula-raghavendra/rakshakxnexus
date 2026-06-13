# Rakshak — AI-Powered CCTV City Intelligence

Real-time CCTV incident detection and autonomous city command system for Hyderabad.  
YOLOv8 computer vision · FastAPI · React · Twilio alerts · Glassmorphism UI

---

## Requirements

- Python 3.10+  →  https://python.org
- Node.js 18+   →  https://nodejs.org
- Git

---

## Running on any laptop

**Just double-click `start.bat`.**

It will:
1. Create a Python virtual environment (`.venv`) if one doesn't exist
2. Install all Python and npm dependencies automatically
3. Start all three services in separate terminal windows

| Service | URL |
|---|---|
| Dashboard (React) | http://localhost:3000 |
| Rakshak CCTV feed | http://localhost:8001 |
| NEXUS API docs | http://localhost:8000/docs |

To stop: close the three terminal windows.

---

## Environment variables

Create `backend/.env.local` with:

```
GEMINI_API_KEY=your_key_here
TWILIO_ACCOUNT_SID=your_sid_here
TWILIO_AUTH_TOKEN=your_token_here
NEXUS_API_KEY=rakshak_2026_a9XkP7mN4vQ2sL8dF5wR1zC6
```

Without these the system runs fine in rules-based fallback mode (no LLM, no calls).

---

## Using the demo

1. Open http://localhost:8001 (Rakshak CCTV)
2. Select a camera slot and upload a video from `test_videos/`
3. Watch detections appear on the city map at http://localhost:3000
4. Click **Analyse incident** on any detection card to see the agent deliberate
5. Click **✓ Resolved** to dismiss

---

## Project structure

```
nexus/
├── backend/          FastAPI city governor (port 8000)
│   ├── api/          REST + WebSocket endpoints
│   ├── governor/     AI decision engine (Gemini / rules fallback)
│   ├── simulation/   City digital twin
│   └── alerts/       Twilio call/SMS dispatcher
├── rakshak/          CCTV inference server (port 8001)
│   ├── api/          Upload, stream, frame endpoints
│   ├── core/         YOLOv8 video processor + classifier
│   └── models/       yolov8n.pt (downloaded on first run)
├── frontend/         React dashboard (port 3000)
│   └── src/
│       └── components/  CityMap, CctvPanel, DecisionFeed, etc.
├── test_videos/      Sample incident videos for demo
└── start.bat         One-click launcher
```
