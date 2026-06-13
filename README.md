# NEXUS — AI City Governor

Autonomous AI governor for Hyderabad. Real-time digital twin + Gemini-powered decision engine.

## Quick Start

### 1. Environment
```bash
cd backend
cp .env.example .env
# Add your GEMINI_API_KEY to .env
```

### 2. Backend
```bash
cd backend
pip install -r requirements.txt
cd ..
python -m uvicorn backend.main:app --reload --port 8000
```

First run fetches Hyderabad OSM data (~30s). Cached after that.

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

### Windows one-click
```
start.bat
```

---

## Demo Flow

1. Open http://localhost:3000 — city appears in normal ops mode
2. Click **SEPT 2024 FLOOD** button in the top bar
3. Watch:
   - Zones shift from green → yellow → orange → red
   - Rakshak CCTV incidents appear on the map (📹 markers)
   - AI Governor decisions stream on the right panel
   - Cascade failures propagate in the bottom graph
   - Resources move toward crisis zones
4. Approve or reject critical decisions (backup power, evacuations) in the feed
5. Click **NORMAL DAY** to reset

---

## Architecture

```
Rakshak (CCTV ML) ──► RakshakAdapter ──┐
                                        ▼
OSM Data ──► CityGraph ──► SimulationEngine ──► CityState
                                        │
                                        ▼
                               AIGovernor (Gemini 1.5 Pro)
                                        │
                            ┌───────────┴────────────┐
                            ▼                        ▼
                      Auto-execute             Pending approval
                      (conf ≥ 0.85)           (critical / low conf)
                            │
                            ▼
                    FastAPI WebSocket ──► React UI
```

## Rakshak Integration

**Mode: replay (default)**  
Replays recorded Sept 2024 Mehdipatnam incident sequence. Incidents fire at real
relative timestamps from that night.

**Mode: live**  
Set `RAKSHAK_MODE=live` and `RAKSHAK_API_URL=http://your-rakshak-host/detections`
in `.env`. Expects Rakshak to emit JSON:
```json
{
  "detections": [{
    "incident_type": "road_flood",
    "lat": 17.3957, "lng": 78.4290,
    "confidence": 0.94,
    "camera_id": "cam_meh_001",
    "severity": "critical",
    "description": "Water rising at underpass",
    "zone_id": "mehdipatnam_up",
    "timestamp": 1725000000.0
  }]
}
```

## External Data Required

| Source | How | Size |
|--------|-----|------|
| Hyderabad road network | `osmnx` fetches from OpenStreetMap API on first run | ~50MB cached |
| Sept 2024 rainfall readings | Embedded in `simulation/engine.py` (from IMD records) | ~2KB |
| Infrastructure positions | Hardcoded from GHMC public data | — |
| Gemini API | `GEMINI_API_KEY` env var | — |

No datasets to download manually.
