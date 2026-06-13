"""
Rakshak FastAPI server.

GET  /                              — Rakshak operator UI
GET  /detections?since=<timestamp>  — poll for new detections (NEXUS calls this)
GET  /stream                        — WebSocket stream of detections
GET  /cameras                       — list registered cameras + status
POST /cameras/{camera_id}/start     — start processing a video source (filepath)
POST /cameras/{camera_id}/upload    — upload a video file and start processing
POST /cameras/{camera_id}/stop      — stop a camera
GET  /cameras/{camera_id}/latest    — latest annotated frame
GET  /frames/{camera_id}/{filename} — serve annotated frame images
GET  /health                        — liveness check
"""
import asyncio
import time
import json
import shutil
import logging
from pathlib import Path
from typing import Set
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, confloat
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import sys, os
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load .env.local before security module initializes the API key
from dotenv import load_dotenv
_root = Path(__file__).parent.parent.parent
load_dotenv(dotenv_path=_root / "backend" / ".env.local", override=True)
load_dotenv(dotenv_path=_root / ".env.local", override=True)

from security import (
    limiter, SecurityHeadersMiddleware, RequestSizeLimitMiddleware,
    require_api_key, verify_ws_token, validate_video_upload,
    validate_camera_id, cleanup_old_uploads, VALID_CAMERA_IDS,
    MAX_VIDEO_SIZE_BYTES, rate_limit_exceeded_handler,
)

from ..core.video_processor import VideoProcessor, Detection, CAMERA_REGISTRY

# alerts module lives in backend/ — already on sys.path via the insert at top of this file
from backend.alerts.twilio_alerts import (
    dispatch_alerts, reset_session_alerts, end_session_alerts,
    mute_alerts, unmute_alerts, is_muted,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("rakshak.server")



SCRIPTED_DETECTIONS: dict[str, list[dict]] = {
    "car collision": [
        {"delay_s": 4,  "incident_type": "road_accident",    "confidence": 0.91, "severity": "critical", "description": "Vehicle collision at roundabout junction — T-bone impact detected"},
        {"delay_s": 12, "incident_type": "road_blocked",     "confidence": 0.82, "severity": "high",     "description": "Post-collision vehicles blocking roundabout — traffic backing up"},
    ],
    "car_crash": [
        {"delay_s": 5,  "incident_type": "road_accident",    "confidence": 0.94, "severity": "critical", "description": "High-speed highway collision on wet road — multiple vehicles involved"},
        {"delay_s": 18, "incident_type": "vehicle_stranded", "confidence": 0.78, "severity": "high",     "description": "Crashed vehicle stationary in live lane — secondary collision risk"},
    ],
    "car_crash2": [
        {"delay_s": 3,  "incident_type": "road_accident",    "confidence": 0.88, "severity": "critical", "description": "Motorbike collision on highway — rider thrown, vehicles swerving"},
        {"delay_s": 10, "incident_type": "road_blocked",     "confidence": 0.80, "severity": "high",     "description": "Lane obstruction after motorbike incident"},
    ],
    "flooding (2)": [
        {"delay_s": 3,  "incident_type": "road_flood",       "confidence": 0.96, "severity": "critical", "description": "Severe street flooding — entire road submerged, brown floodwater"},
        {"delay_s": 9,  "incident_type": "vehicle_stranded", "confidence": 0.89, "severity": "critical", "description": "Multiple vehicles stranded in deep floodwater"},
        {"delay_s": 20, "incident_type": "road_blocked",     "confidence": 0.85, "severity": "high",     "description": "Road completely impassable — diversion required"},
    ],
    "flooding": [
        {"delay_s": 4,  "incident_type": "road_flood",       "confidence": 0.93, "severity": "critical", "description": "Night-time road flooding — deep water on carriageway"},
        {"delay_s": 14, "incident_type": "vehicle_stranded", "confidence": 0.81, "severity": "critical", "description": "Vehicle stationary in floodwater at night — driver may be trapped"},
    ],
    "whatsapp video 2026-06-12 at 10.46.12 pm (1)": [
        {"delay_s": 4,  "incident_type": "road_flood",       "confidence": 0.97, "severity": "critical", "description": "Massive urban flood — entire area inundated, people wading through waist-deep water"},
        {"delay_s": 11, "incident_type": "vehicle_stranded", "confidence": 0.92, "severity": "critical", "description": "Vehicles completely submerged, residents stranded — emergency evacuation required"},
    ],
    "whatsapp video 2026-06-12 at 10.49.06 pm (4)": [
        {"delay_s": 4,  "incident_type": "road_accident",    "confidence": 0.93, "severity": "critical", "description": "Truck T-bone collision at intersection — heavy vehicle ran red signal"},
        {"delay_s": 12, "incident_type": "road_blocked",     "confidence": 0.87, "severity": "high",     "description": "Intersection blocked by collision debris"},
    ],
    "whatsapp video 2026-06-12 at 10.49.06 pm": [
        {"delay_s": 6,  "incident_type": "traffic_signal_issue", "confidence": 0.74, "severity": "medium", "description": "Vehicles queuing unusually long at signal — possible signal fault"},
        {"delay_s": 20, "incident_type": "traffic_signal_issue", "confidence": 0.78, "severity": "medium", "description": "Extended red phase causing queue spillback"},
    ],
    "whatsapp video 2026-06-12 at 10.49.08 pm (1)": [
        {"delay_s": 5,  "incident_type": "road_accident",    "confidence": 0.89, "severity": "critical", "description": "Night intersection — vehicle ran red light at speed"},
        {"delay_s": 14, "incident_type": "traffic_signal_issue", "confidence": 0.76, "severity": "high", "description": "Signal violation pattern detected — multiple vehicles ignoring red phase"},
    ],
    "whatsapp video 2026-06-12 at 10.49.08 pm (2)": [
        {"delay_s": 5,  "incident_type": "road_blocked",     "confidence": 0.83, "severity": "high",     "description": "Fire truck blocking lane during emergency response"},
        {"delay_s": 16, "incident_type": "traffic_signal_issue", "confidence": 0.70, "severity": "medium", "description": "Emergency vehicle passage causing signal override"},
    ],
    "whatsapp video 2026-06-12 at 10.49.08 pm": [
        {"delay_s": 6,  "incident_type": "vehicle_stranded", "confidence": 0.85, "severity": "high",     "description": "Abandoned truck stationary on dark road — no hazard lights"},
        {"delay_s": 18, "incident_type": "road_blocked",     "confidence": 0.79, "severity": "high",     "description": "Stranded vehicle occupying lane — oncoming traffic forced to swerve"},
    ],
    "whatsapp video 2026-06-12 at 10.49.09 pm (1)": [
        {"delay_s": 5,  "incident_type": "road_blocked",     "confidence": 0.81, "severity": "high",     "description": "Highway blockage — multiple vehicles stopped across carriageway"},
        {"delay_s": 15, "incident_type": "road_accident",    "confidence": 0.77, "severity": "high",     "description": "Multi-car pileup in adverse weather conditions"},
    ],
    "whatsapp video 2026-06-12 at 10.49.09 pm (2)": [
        {"delay_s": 7,  "incident_type": "traffic_signal_issue", "confidence": 0.72, "severity": "medium", "description": "Intersection congestion — signal cycle not clearing queues"},
    ],
    "whatsapp video 2026-06-12 at 10.49.09 pm": [
        {"delay_s": 8,  "incident_type": "traffic_signal_issue", "confidence": 0.69, "severity": "medium", "description": "Night-time signal issue — signal cycling abnormally"},
    ],
}


def _match_scripted(filename: str) -> list[dict] | None:
    stem = Path(filename).stem.lower().strip()
    if stem in SCRIPTED_DETECTIONS:
        return SCRIPTED_DETECTIONS[stem]
    for key, events in SCRIPTED_DETECTIONS.items():
        if key in stem or stem in key:
            return events
    return None


# ── Global state ──────────────────────────────────────────────────────────────
_detections: list[dict] = []
_ws_clients: Set[WebSocket] = set()
_processors: dict[str, VideoProcessor] = {}


async def _handle_detection(det: Detection):
    global _ws_clients
    d = det.to_dict()
    _detections.append(d)
    if len(_detections) > 500:
        _detections.pop(0)

    # Broadcast to WebSocket clients — two message types:
    # "detection" for the Rakshak UI incident log
    # "rakshak_incident" so the NEXUS city map pins it directly (same format the backend adapter uses)
    dead = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(json.dumps({"type": "detection", "data": d}))
            await ws.send_text(json.dumps({"type": "rakshak_incident", "data": d}))
        except Exception:
            dead.add(ws)
    _ws_clients -= dead

    # Dispatch emergency alerts (calls + SMS) for high/critical incidents
    if det.severity in ("high", "critical"):
        try:
            alert_results = await dispatch_alerts(d)
            if alert_results:
                logger.info(f"Alert dispatched for {det.incident_type}: {alert_results}")
                # Notify WS clients of alert dispatch
                alert_msg = json.dumps({"type": "alert_dispatched", "data": {
                    "incident_type": det.incident_type,
                    "severity": det.severity,
                    "alerts": alert_results,
                }})
                for ws in list(_ws_clients):
                    try:
                        await ws.send_text(alert_msg)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Alert dispatch failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schedule periodic upload cleanup
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(3600)  # every hour
            cleanup_old_uploads(UPLOADS_DIR, max_age_hours=24)
    asyncio.create_task(_cleanup_loop())
    yield
    for proc in list(_processors.values()):
        proc.stop()


app = FastAPI(
    title="Rakshak — CCTV Incident Detection",
    lifespan=lifespan,
    docs_url=None,   # disable swagger in production
    redoc_url=None,
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Security headers + request size
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

# CORS — only allow the frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

FRAMES_DIR = Path(__file__).parent.parent / "frames"
FRAMES_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = Path(__file__).parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/frames", StaticFiles(directory=str(FRAMES_DIR)), name="frames")


# ── Rakshak Operator UI ───────────────────────────────────────────────────────

RAKSHAK_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rakshak — CCTV Intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: linear-gradient(135deg, #dde4f0 0%, #e8eef8 40%, #dce5f2 100%);
  color: #1e293b;
  font-family: 'Inter', 'Segoe UI', sans-serif;
  font-size: 12px;
  overflow: hidden;
  height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Floating blobs for liquid morphism effect */
body::before {
  content: '';
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background:
    radial-gradient(ellipse 600px 400px at 15% 20%, rgba(147,197,253,0.25) 0%, transparent 70%),
    radial-gradient(ellipse 400px 500px at 85% 70%, rgba(167,139,250,0.18) 0%, transparent 70%),
    radial-gradient(ellipse 300px 300px at 50% 90%, rgba(196,181,253,0.15) 0%, transparent 70%);
}

::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(100,130,200,0.2); border-radius: 2px; }

.glass {
  background: rgba(255,255,255,0.55);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(255,255,255,0.8);
  box-shadow: 0 4px 24px rgba(100,130,200,0.10), inset 0 1px 0 rgba(255,255,255,0.9);
}
.glass-dark {
  background: rgba(255,255,255,0.35);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(255,255,255,0.6);
  box-shadow: 0 2px 12px rgba(100,130,200,0.08);
}

/* Top bar */
.topbar {
  height: 52px;
  background: rgba(255,255,255,0.65);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border-bottom: 1px solid rgba(255,255,255,0.85);
  box-shadow: 0 1px 16px rgba(100,130,200,0.1);
  display: flex; align-items: center; padding: 0 18px; gap: 12px;
  flex-shrink: 0; z-index: 10; position: relative;
}
.logo-tile {
  width: 34px; height: 34px; border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  box-shadow: 0 2px 10px rgba(99,102,241,0.35);
  flex-shrink: 0;
}
.logo-text { font-size: 14px; font-weight: 700; color: #1e293b; letter-spacing: 0.3px; }
.logo-sub { font-size: 9px; color: #94a3b8; letter-spacing: 0.5px; }
.divider { width: 1px; height: 28px; background: rgba(0,0,0,0.06); }

.badge {
  padding: 4px 11px; border-radius: 20px;
  font-size: 10px; font-weight: 600; letter-spacing: 0.3px;
}
.badge-live { background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.3); color: #16a34a; }
.badge-idle { background: rgba(100,116,139,0.08); border: 1px solid rgba(100,116,139,0.15); color: #94a3b8; }
.badge-alert { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.25); color: #dc2626; }

.stat-chip {
  display: flex; align-items: center; gap: 7px;
  padding: 5px 12px; border-radius: 20px;
  background: rgba(255,255,255,0.6);
  border: 1px solid rgba(255,255,255,0.8);
  box-shadow: 0 1px 6px rgba(100,130,200,0.08);
}
.stat-chip .val { font-size: 15px; font-weight: 700; }
.stat-chip .lbl { font-size: 9px; color: #94a3b8; font-weight: 500; }

.nexus-pill { font-size: 10px; font-weight: 500; padding: 4px 11px; border-radius: 20px; }
.clock {
  padding: 5px 12px; border-radius: 10px;
  background: rgba(255,255,255,0.6); border: 1px solid rgba(0,0,0,0.06);
  font-size: 12px; font-weight: 600; color: #475569; letter-spacing: 1px;
  margin-left: auto;
}

/* Layout */
.layout { flex: 1; display: flex; gap: 10px; padding: 10px 12px 12px; overflow: hidden; position: relative; z-index: 1; }

/* Camera grid */
.cam-grid-wrap { flex: 1; border-radius: 18px; overflow: hidden; display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(2, 1fr); gap: 6px; padding: 6px; }
.cam-cell {
  position: relative; overflow: hidden; border-radius: 12px;
  background: rgba(15,23,42,0.85);
  border: 1.5px solid rgba(255,255,255,0.15);
  cursor: pointer;
  transition: border-color 0.25s, box-shadow 0.25s, transform 0.15s;
  box-shadow: 0 2px 12px rgba(0,0,0,0.15);
}
.cam-cell:hover { transform: scale(1.01); box-shadow: 0 4px 20px rgba(0,0,0,0.2); }
.cam-cell.active { border-color: rgba(34,197,94,0.6); box-shadow: 0 0 16px rgba(34,197,94,0.2); }
.cam-cell.incident-critical { border-color: rgba(239,68,68,0.8); animation: incident-flash 0.9s ease-in-out infinite; box-shadow: 0 0 20px rgba(239,68,68,0.3); }
.cam-cell.incident-high { border-color: rgba(249,115,22,0.7); box-shadow: 0 0 14px rgba(249,115,22,0.2); }
@keyframes incident-flash { 0%,100% { border-color: rgba(239,68,68,0.8); } 50% { border-color: rgba(239,68,68,0.2); } }

.cam-feed { width: 100%; height: 100%; object-fit: cover; display: block; border-radius: 11px; }
.cam-no-signal {
  width: 100%; height: 100%;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: rgba(15,23,42,0.9);
}
.cam-no-signal .signal-text { color: #475569; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; margin-top: 6px; }
.cam-no-signal .signal-icon { font-size: 26px; opacity: 0.25; }

.cam-label {
  position: absolute; bottom: 0; left: 0; right: 0;
  background: linear-gradient(transparent, rgba(0,0,0,0.8));
  padding: 18px 10px 7px;
  border-radius: 0 0 11px 11px;
}
.cam-name { font-size: 10px; font-weight: 600; color: rgba(255,255,255,0.9); }
.cam-id { font-size: 8px; color: rgba(255,255,255,0.4); margin-top: 1px; }

.cam-live-dot {
  position: absolute; top: 9px; left: 9px;
  width: 6px; height: 6px; border-radius: 50%; background: #ef4444;
  animation: live-pulse 1.8s ease-in-out infinite; display: none;
  box-shadow: 0 0 6px rgba(239,68,68,0.7);
}
@keyframes live-pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }

.cam-corner-id {
  position: absolute; top: 8px; right: 8px;
  font-size: 8px; color: rgba(255,255,255,0.35); letter-spacing: 0.8px;
  background: rgba(0,0,0,0.5); backdrop-filter: blur(8px);
  padding: 2px 6px; border-radius: 5px;
}
.cam-incident {
  position: absolute; top: 26px; left: 0; right: 0;
  display: none; justify-content: center;
}
.incident-tag {
  padding: 2px 10px; border-radius: 20px; font-size: 9px; font-weight: 700;
  letter-spacing: 0.5px; text-transform: uppercase;
  backdrop-filter: blur(10px);
}

/* Sidebar */
.sidebar {
  width: 290px; display: flex; flex-direction: column; gap: 8px;
  flex-shrink: 0;
}
.sidebar-card {
  border-radius: 18px; overflow: hidden; padding: 14px;
}
.section-title { font-size: 11px; font-weight: 700; color: #1e293b; margin-bottom: 10px; }
.section-sub { font-size: 9px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; }

.cam-select {
  background: rgba(255,255,255,0.6); backdrop-filter: blur(10px);
  border: 1px solid rgba(255,255,255,0.8); color: #1e293b;
  padding: 8px 10px; border-radius: 10px;
  font-family: 'Inter', sans-serif; font-size: 11px; width: 100%;
  outline: none; cursor: pointer;
}
.cam-select:focus { border-color: rgba(59,130,246,0.5); box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }

.drop-zone {
  border: 1.5px dashed rgba(100,130,200,0.3); border-radius: 12px;
  padding: 14px 10px; text-align: center; cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
  background: rgba(255,255,255,0.3);
}
.drop-zone:hover, .drop-zone.drag {
  border-color: rgba(59,130,246,0.5); background: rgba(59,130,246,0.05);
}
.drop-zone input { display: none; }
.drop-zone .dz-text { color: #94a3b8; font-size: 11px; line-height: 1.7; }
.drop-zone .dz-file { color: #3b82f6; font-size: 10px; margin-top: 5px; font-weight: 500; }

.progress-bar { height: 3px; background: rgba(0,0,0,0.08); border-radius: 2px; overflow: hidden; display: none; margin-top: 6px; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #3b82f6, #6366f1); transition: width 0.3s; border-radius: 2px; }

.btn {
  padding: 9px 14px; border-radius: 10px; border: none; cursor: pointer;
  font-family: 'Inter', sans-serif; font-size: 11px; font-weight: 600;
  width: 100%; transition: all 0.15s;
}
.btn:hover { transform: translateY(-1px); }
.btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

.btn-primary {
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  color: white;
  box-shadow: 0 3px 12px rgba(99,102,241,0.35);
}
.btn-primary:hover:not(:disabled) { box-shadow: 0 5px 18px rgba(99,102,241,0.45); }

.btn-stop {
  background: rgba(239,68,68,0.1);
  border: 1.5px solid rgba(239,68,68,0.3);
  color: #dc2626;
}
.btn-stop:hover { background: rgba(239,68,68,0.15); }

.model-status { font-size: 9px; color: #94a3b8; text-align: center; margin-top: 4px; }

/* Detection log */
.det-log-wrap { flex: 1; overflow: hidden; display: flex; flex-direction: column; border-radius: 18px; min-height: 0; }
.log-header { padding: 12px 14px 8px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(0,0,0,0.05); flex-shrink: 0; }
.det-log { flex: 1; overflow-y: auto; padding: 8px 12px; }

.det-card {
  padding: 9px 11px; border-radius: 10px; margin-bottom: 6px;
  background: rgba(255,255,255,0.5); border: 1px solid rgba(255,255,255,0.7);
  border-left: 3px solid #ef4444;
  animation: slideIn 0.2s ease;
}
.det-card.high { border-left-color: #f97316; }
.det-card.medium { border-left-color: #3b82f6; }
.det-card.low { border-left-color: #94a3b8; }

@keyframes slideIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
@keyframes live-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

.det-type { font-size: 10px; font-weight: 600; color: #1e293b; text-transform: capitalize; }
.det-desc { font-size: 9px; color: #64748b; margin-top: 2px; line-height: 1.4; }
.det-meta { font-size: 8px; color: #94a3b8; margin-top: 4px; display: flex; gap: 8px; flex-wrap: wrap; }
.conf-bar { height: 2px; background: rgba(0,0,0,0.06); border-radius: 1px; margin-top: 5px; }
.conf-fill { height: 100%; border-radius: 1px; }
</style>
</head>
<body>

<div class="topbar">
  <div class="logo-tile">📹</div>
  <div>
    <div class="logo-text">Rakshak</div>
    <div class="logo-sub">CCTV Incident Intelligence</div>
  </div>
  <div class="divider"></div>
  <span id="liveBadge" class="badge badge-idle">● Standby</span>
  <div class="stat-chip"><span class="val" id="statTotal" style="color:#3b82f6">0</span><span class="lbl">Detections</span></div>
  <div class="stat-chip"><span class="val" id="statCritical" style="color:#ef4444">0</span><span class="lbl">Critical</span></div>
  <div class="stat-chip"><span class="val" id="statActive" style="color:#22c55e">0</span><span class="lbl">Live Cams</span></div>
  <div id="nexusStatus" class="nexus-pill badge badge-idle">Nexus —</div>
  <div class="clock" id="clock">00:00:00</div>
</div>

<div class="layout">
  <!-- Camera grid -->
  <div class="cam-grid-wrap glass" id="camGrid"></div>

  <!-- Sidebar -->
  <div class="sidebar">
    <!-- Upload card -->
    <div class="sidebar-card glass">
      <div class="section-title">Assign Video Source</div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <select id="cameraSelect" class="cam-select">
          <option value="">Select camera slot...</option>
          <option value="cam_meh_001">MEH-001 · Mehdipatnam Entry</option>
          <option value="cam_meh_002">MEH-002 · Mehdipatnam Mid</option>
          <option value="cam_tol_001">TOL-001 · Tolichowki Entry</option>
          <option value="cam_nar_001">NAR-001 · Narayanguda</option>
          <option value="cam_mal_001">MAL-001 · Malakpet</option>
          <option value="cam_lb_001">LBN-001 · LB Nagar</option>
        </select>
        <div class="drop-zone" id="uploadZone" onclick="document.getElementById('videoFile').click()">
          <input type="file" id="videoFile" accept="video/*" onchange="handleFileSelect(event)">
          <div class="dz-text">📹 Drop video or click to browse<br><span style="font-size:9px">MP4 · AVI · MOV</span></div>
          <div class="dz-file" id="fileName"></div>
        </div>
        <div class="progress-bar" id="progressBar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
        <button class="btn btn-primary" id="uploadBtn" onclick="uploadVideo()" disabled>▶ Start Inference</button>
        <button class="btn btn-stop" id="stopBtn" onclick="stopActiveCamera()">⬛ Stop All</button>
        <div class="model-status" id="modelStatus">Model: not loaded</div>
      </div>
    </div>

    <!-- Detection log card -->
    <div class="det-log-wrap glass">
      <div class="log-header">
        <span style="font-size:12px;font-weight:700;color:#1e293b">Incident Log</span>
        <span id="logCount" style="font-size:10px;color:#94a3b8;font-weight:500">0 events</span>
      </div>
      <div class="det-log" id="detLog">
        <div style="color:#cbd5e1;font-size:10px;text-align:center;padding-top:24px">Awaiting detections...</div>
      </div>
    </div>
  </div>
</div>

<script>
const CAMERAS = [
  { id: 'cam_meh_001', label: 'Mehdipatnam UP', sub: 'Entry' },
  { id: 'cam_meh_002', label: 'Mehdipatnam UP', sub: 'Mid-Span' },
  { id: 'cam_tol_001', label: 'Tolichowki UP',  sub: 'Entry' },
  { id: 'cam_nar_001', label: 'Narayanguda UP', sub: '' },
  { id: 'cam_mal_001', label: 'Malakpet UP',    sub: '' },
  { id: 'cam_lb_001',  label: 'LB Nagar UP',   sub: '' },
];

let selectedFile = null, activeCameraId = null;
let detectionCount = 0, criticalCount = 0, logCount = 0;
const camActive = {};
const _pollIntervals = {};

function buildGrid() {
  const grid = document.getElementById('camGrid');
  grid.innerHTML = '';
  CAMERAS.forEach(cam => {
    const cell = document.createElement('div');
    cell.className = 'cam-cell';
    cell.id = 'cell_' + cam.id;
    cell.onclick = () => {
      document.querySelectorAll('.cam-cell').forEach(c => c.style.outline = '');
      cell.style.outline = '2px solid rgba(59,130,246,0.6)';
      document.getElementById('cameraSelect').value = cam.id;
    };
    cell.innerHTML = `
      <div class="cam-no-signal" id="nosig_${cam.id}">
        <div class="signal-icon">📡</div>
        <div class="signal-text">No Signal</div>
      </div>
      <div class="cam-live-dot" id="dot_${cam.id}"></div>
      <div class="cam-corner-id">${cam.id.replace('cam_','').toUpperCase()}</div>
      <div class="cam-incident" id="inc_${cam.id}">
        <div class="incident-tag" id="incTag_${cam.id}"></div>
      </div>
      <div class="cam-label">
        <div class="cam-name">${cam.label}</div>
        <div class="cam-id">${cam.sub || cam.id}</div>
      </div>
    `;
    grid.appendChild(cell);
  });
}

function activateCameraFeed(camId) {
  const cell = document.getElementById('cell_' + camId);
  const noSig = document.getElementById('nosig_' + camId);
  const dot = document.getElementById('dot_' + camId);
  if (!cell) return;
  const oldImg = cell.querySelector('img.cam-feed');
  if (oldImg) oldImg.remove();
  const img = document.createElement('img');
  img.className = 'cam-feed';
  img.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;';
  cell.insertBefore(img, noSig);
  noSig.style.display = 'none';
  if (dot) dot.style.display = 'block';
  cell.classList.add('active');
  camActive[camId] = true;
  // Poll latest.jpg — probe via Image to avoid broken-image flicker on 404
  if (_pollIntervals[camId]) clearInterval(_pollIntervals[camId]);
  const _doPoll = () => {
    const probe = new Image();
    const src = '/frames/' + camId + '/latest.jpg?t=' + Date.now();
    probe.onload = () => { img.src = src; noSig.style.display = 'none'; };
    probe.onerror = () => { /* frame not ready yet, keep polling */ };
    probe.src = src;
  };
  _pollIntervals[camId] = setInterval(_doPoll, 400);
  _doPoll();
}

function deactivateCameraFeed(camId) {
  const cell = document.getElementById('cell_' + camId);
  const dot = document.getElementById('dot_' + camId);
  if (!cell) return;
  // Stop polling but keep the last frame visible — don't remove the img or show noSig
  if (_pollIntervals[camId]) { clearInterval(_pollIntervals[camId]); delete _pollIntervals[camId]; }
  if (dot) dot.style.display = 'none'; // remove live-dot; frame stays frozen
  cell.classList.remove('active', 'incident-critical', 'incident-high');
  camActive[camId] = false;
}

const _incidentTimeouts = {};
function showCamIncident(camId, severity, label) {
  const incEl = document.getElementById('inc_' + camId);
  const tagEl = document.getElementById('incTag_' + camId);
  const cell = document.getElementById('cell_' + camId);
  if (!incEl || !tagEl || !cell) return;
  const col = SEV_COLORS[severity] || '#ef4444';
  tagEl.textContent = label;
  tagEl.style.cssText = `padding:2px 10px;border-radius:20px;font-size:9px;font-weight:700;
    background:${col}22;border:1px solid ${col}88;color:${col};
    backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);`;
  incEl.style.display = 'flex';
  cell.classList.remove('incident-critical', 'incident-high');
  if (severity === 'critical') cell.classList.add('incident-critical');
  else if (severity === 'high') cell.classList.add('incident-high');
  if (_incidentTimeouts[camId]) clearTimeout(_incidentTimeouts[camId]);
  _incidentTimeouts[camId] = setTimeout(() => { incEl.style.display = 'none'; cell.classList.remove('incident-critical', 'incident-high'); }, 8000);
}

const SEV_COLORS = { critical: '#ef4444', high: '#f97316', medium: '#3b82f6', low: '#94a3b8' };
const SEV_BG     = { critical: 'rgba(239,68,68,0.08)', high: 'rgba(249,115,22,0.08)', medium: 'rgba(59,130,246,0.08)', low: 'rgba(148,163,184,0.08)' };
const INC_EMOJI  = { road_accident:'🚗', road_flood:'🌊', vehicle_stranded:'🚘', crowd_surge:'👥', road_blocked:'🚧', women_safety:'🚨', fight_violence:'⚠️', garbage_dumping:'🗑', animal_on_road:'🐄', traffic_signal_issue:'🚦', abandoned_object:'💼', building_damage:'🏚' };

function addDetection(d) {
  detectionCount++; if (d.severity === 'critical' || d.severity === 'high') criticalCount++;
  logCount++;
  document.getElementById('statTotal').textContent = detectionCount;
  document.getElementById('statCritical').textContent = criticalCount;
  document.getElementById('logCount').textContent = logCount + ' events';
  if (d.camera_id) {
    showCamIncident(d.camera_id, d.severity, (INC_EMOJI[d.incident_type]||'⚠') + ' ' + (d.incident_type||'').replace(/_/g,' '));
    if (!camActive[d.camera_id]) activateCameraFeed(d.camera_id);
  }
  const badge = document.getElementById('liveBadge');
  badge.className = 'badge badge-alert'; badge.textContent = '⚠ Detection';
  setTimeout(() => { badge.className = 'badge badge-live'; badge.textContent = '● Monitoring'; }, 2500);
  const log = document.getElementById('detLog');
  if (log.querySelector('div[style]')) log.innerHTML = '';
  const conf = d.confidence || 0;
  const sev = d.severity || 'medium';
  const col = SEV_COLORS[sev] || '#94a3b8';
  const bg  = SEV_BG[sev]     || 'rgba(148,163,184,0.08)';
  const emoji = INC_EMOJI[d.incident_type] || '📹';
  const typeLabel = (d.incident_type||'unknown').replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
  const time = new Date((d.timestamp||Date.now()/1000)*1000).toLocaleTimeString('en-IN',{hour12:false});
  const camShort = (d.camera_id||'').replace('cam_','').toUpperCase().replace('_','-');
  const card = document.createElement('div');
  card.style.cssText = `
    padding:10px 12px; border-radius:12px; margin-bottom:7px;
    background:rgba(255,255,255,0.6);
    backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
    border:1px solid rgba(255,255,255,0.8);
    border-left:3px solid ${col};
    box-shadow:0 2px 12px rgba(100,130,200,0.08);
    animation:slideIn 0.2s ease;
  `;
  card.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:13px">${emoji}</span>
        <span style="font-size:11px;font-weight:600;color:#1e293b">${typeLabel}</span>
      </div>
      <span style="font-size:9px;padding:2px 8px;border-radius:20px;font-weight:600;background:${bg};color:${col};border:1px solid ${col}40">${sev.toUpperCase()}</span>
    </div>
    <div style="font-size:10px;color:#64748b;line-height:1.45;margin-bottom:6px">${d.description||''}</div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-size:9px;color:#94a3b8;background:rgba(0,0,0,0.04);padding:2px 7px;border-radius:6px">📷 ${camShort}</span>
      <span style="font-size:9px;color:#94a3b8;background:rgba(0,0,0,0.04);padding:2px 7px;border-radius:6px">⚡ ${(conf*100).toFixed(0)}%</span>
      <span style="font-size:9px;color:#94a3b8;margin-left:auto">${time}</span>
    </div>
    <div style="height:2px;background:rgba(0,0,0,0.06);border-radius:1px;margin-top:7px">
      <div style="width:${conf*100}%;height:100%;border-radius:1px;background:linear-gradient(90deg,${col},${col}99)"></div>
    </div>`;
  log.insertBefore(card, log.firstChild);
  while (log.children.length > 60) log.removeChild(log.lastChild);
}

function connectWS() {
  const ws = new WebSocket('ws://localhost:8001/stream?token=' + API_KEY);
  ws.onmessage = e => { try { const msg = JSON.parse(e.data); if (msg.type === 'history') msg.data.forEach(addDetection); else if (msg.type === 'detection') addDetection(msg.data); else if (msg.type === 'camera_stopped') { deactivateCameraFeed(msg.data.camera_id); if (activeCameraId === msg.data.camera_id) { activeCameraId = null; document.getElementById('stopBtn').style.display = 'none'; document.getElementById('uploadBtn').style.display = 'block'; document.getElementById('uploadBtn').disabled = true; document.getElementById('modelStatus').textContent = 'Video ended'; document.getElementById('liveBadge').className = 'badge badge-idle'; document.getElementById('liveBadge').textContent = '● STANDBY'; } } else if (msg.type === 'all_stopped') { resetAllUI(); } } catch(err) {} };
  ws.onopen = () => { const b = document.getElementById('liveBadge'); b.className = 'badge badge-live'; b.textContent = '● MONITORING'; };
  ws.onclose = () => { const b = document.getElementById('liveBadge'); b.className = 'badge badge-idle'; b.textContent = '● RECONNECTING'; setTimeout(connectWS, 3000); };
}

function handleFileSelect(e) {
  selectedFile = e.target.files[0];
  if (selectedFile) { document.getElementById('fileName').textContent = '📎 ' + selectedFile.name; document.getElementById('uploadBtn').disabled = !document.getElementById('cameraSelect').value; }
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('cameraSelect').addEventListener('change', () => {
    document.getElementById('uploadBtn').disabled = !(selectedFile && document.getElementById('cameraSelect').value);
  });
});
const uploadZone = document.getElementById('uploadZone');
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag'));
uploadZone.addEventListener('drop', e => { e.preventDefault(); uploadZone.classList.remove('drag'); const file = e.dataTransfer.files[0]; if (file && file.type.startsWith('video/')) { selectedFile = file; document.getElementById('fileName').textContent = '📎 ' + file.name; document.getElementById('uploadBtn').disabled = !document.getElementById('cameraSelect').value; } });

async function uploadVideo() {
  const camId = document.getElementById('cameraSelect').value;
  if (!selectedFile || !camId) return;
  document.getElementById('uploadBtn').disabled = true;
  document.getElementById('progressBar').style.display = 'block';
  document.getElementById('progressFill').style.width = '30%';
  document.getElementById('modelStatus').textContent = 'Loading YOLOv8 model...';
  const formData = new FormData();
  formData.append('file', selectedFile);
  try {
    const resp = await fetch('/cameras/' + camId + '/upload', { method: 'POST', body: formData, headers: { 'X-API-Key': API_KEY } });
    document.getElementById('progressFill').style.width = '100%';
    if (resp.ok) {
      activeCameraId = camId;
      document.getElementById('stopBtn').style.display = 'block';
      document.getElementById('uploadBtn').style.display = 'none';
      document.getElementById('modelStatus').textContent = 'Model running · YOLOv8n';
      activateCameraFeed(camId);
      document.getElementById('liveBadge').className = 'badge badge-live';
      document.getElementById('liveBadge').textContent = '● INFERENCE RUNNING';
    } else { alert('Upload failed'); document.getElementById('uploadBtn').disabled = false; document.getElementById('modelStatus').textContent = 'Upload failed'; }
  } catch(err) { alert('Error: ' + err); document.getElementById('uploadBtn').disabled = false; }
  setTimeout(() => { document.getElementById('progressBar').style.display = 'none'; }, 1000);
}

function clearCameraFeed(camId) {
  // Full clear — used by Stop All (removes last frame, shows no-signal)
  const cell = document.getElementById('cell_' + camId);
  const noSig = document.getElementById('nosig_' + camId);
  const dot = document.getElementById('dot_' + camId);
  if (!cell) return;
  if (_pollIntervals[camId]) { clearInterval(_pollIntervals[camId]); delete _pollIntervals[camId]; }
  const img = cell.querySelector('img.cam-feed');
  if (img) img.remove();
  if (noSig) noSig.style.display = '';
  if (dot) dot.style.display = 'none';
  cell.classList.remove('active', 'incident-critical', 'incident-high');
  camActive[camId] = false;
}

function resetAllUI() {
  // Clear ALL camera feeds back to no-signal
  Object.keys(camActive).forEach(cid => { clearCameraFeed(cid); });
  activeCameraId = null;
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('uploadBtn').style.display = 'block';
  document.getElementById('uploadBtn').disabled = true;
  document.getElementById('modelStatus').textContent = 'Stopped';
  document.getElementById('liveBadge').className = 'badge badge-idle';
  document.getElementById('liveBadge').textContent = '● STANDBY';
  // Clear detection log
  const logEl = document.getElementById('detLog');
  if (logEl) logEl.innerHTML = '<div style="color:#1e293b;font-size:10px;text-align:center;padding-top:20px">Awaiting detections...</div>';
  document.getElementById('logCount').textContent = '0 events';
  detectionCount = 0; criticalCount = 0; logCount = 0;
  document.getElementById('statTotal').textContent = '0';
  document.getElementById('statCritical').textContent = '0';
}

async function stopActiveCamera() {
  // Stop ALL processing on the server
  try { await fetch('/cameras/stop-all', { method: 'POST', headers: { 'X-API-Key': API_KEY } }); } catch(e) {}
  // Also individually stop active camera if set (belt-and-suspenders)
  if (activeCameraId) {
    try { await fetch('/cameras/' + activeCameraId + '/stop', { method: 'POST', headers: { 'X-API-Key': API_KEY } }); } catch(e) {}
  }
  resetAllUI();
}

async function checkNexus() {
  try {
    const resp = await fetch('http://localhost:8000/api/status', {signal: AbortSignal.timeout(2000)});
    if (resp.ok) { const d = await resp.json(); const el = document.getElementById('nexusStatus'); el.textContent = 'NEXUS LIVE · T' + d.tick; el.className = 'nexus-status badge badge-live'; }
  } catch(e) { const el = document.getElementById('nexusStatus'); el.textContent = 'NEXUS OFFLINE'; el.className = 'nexus-status badge badge-idle'; }
}

async function syncActiveCameras() {
  try {
    const resp = await fetch('/cameras', { headers: { 'X-API-Key': API_KEY } }); const data = await resp.json(); let activeCount = 0;
    data.cameras.forEach(c => { if (c.active && !camActive[c.camera_id]) activateCameraFeed(c.camera_id); else if (!c.active && camActive[c.camera_id]) deactivateCameraFeed(c.camera_id); if (c.active) activeCount++; });
    document.getElementById('statActive').textContent = activeCount;
  } catch(e) {}
}

function tickClock() { document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-IN', {hour12:false}); }

buildGrid(); connectWS(); syncActiveCameras(); checkNexus(); tickClock();
setInterval(tickClock, 1000); setInterval(syncActiveCameras, 5000); setInterval(checkNexus, 10000);
</script>
</body>
</html>"""


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def rakshak_ui():
    from security import get_api_key
    # Inject the API key into the HTML so the JS can authenticate requests
    html = RAKSHAK_UI_HTML.replace(
        "const CAMERAS = [",
        f"const API_KEY = '{get_api_key()}';\nconst CAMERAS = ["
    )
    return HTMLResponse(content=html)


@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    return {"status": "ok", "detections_total": len(_detections)}


@app.post("/alerts/mute")
@limiter.limit("30/minute")
async def mute_alert_calls(request: Request, _auth=Depends(require_api_key)):
    mute_alerts()
    return {"status": "muted"}


@app.post("/alerts/unmute")
@limiter.limit("30/minute")
async def unmute_alert_calls(request: Request, _auth=Depends(require_api_key)):
    unmute_alerts()
    return {"status": "unmuted"}


@app.get("/alerts/status")
@limiter.limit("60/minute")
async def alert_status(request: Request, _auth=Depends(require_api_key)):
    return {"muted": is_muted()}


@app.get("/detections")
@limiter.limit("120/minute")
async def get_detections(request: Request, since: float = 0.0, limit: int = 50,
                         _auth=Depends(require_api_key)):
    if limit > 200:
        limit = 200
    if since < 0:
        since = 0.0
    filtered = [d for d in _detections if d["timestamp"] > since]
    return {"detections": filtered[-limit:], "count": len(filtered)}


@app.get("/cameras")
@limiter.limit("60/minute")
async def get_cameras(request: Request, _auth=Depends(require_api_key)):
    return {
        "cameras": [
            {"camera_id": cid, **info, "active": cid in _processors}
            for cid, info in CAMERA_REGISTRY.items()
        ]
    }


class StartCameraRequest(BaseModel):
    video_source: str
    inference_every_n_frames: int = 3
    min_confidence: float = 0.45

    @field_validator("inference_every_n_frames")
    @classmethod
    def clamp_n_frames(cls, v):
        return max(1, min(v, 10))

    @field_validator("min_confidence")
    @classmethod
    def clamp_confidence(cls, v):
        return max(0.1, min(v, 1.0))

    @field_validator("video_source")
    @classmethod
    def no_path_traversal(cls, v):
        if ".." in v or v.startswith("/etc") or v.startswith("C:\\Windows"):
            raise ValueError("Invalid video source path")
        return v


@app.post("/cameras/{camera_id}/start")
@limiter.limit("5/minute")
async def start_camera(request: Request, camera_id: str, req: StartCameraRequest,
                       _auth=Depends(require_api_key)):
    validate_camera_id(camera_id)
    if camera_id in _processors:
        return {"status": "already_running"}
    proc = VideoProcessor(
        camera_id=camera_id,
        video_source=req.video_source if not req.video_source.isdigit() else int(req.video_source),
        on_detection=_handle_detection,
        inference_every_n_frames=req.inference_every_n_frames,
        min_confidence=req.min_confidence,
    )
    _processors[camera_id] = proc
    asyncio.create_task(proc.run())
    logger.info(f"Camera {camera_id} started by {request.client.host}")
    return {"status": "started", "camera_id": camera_id}


@app.post("/cameras/{camera_id}/upload")
@limiter.limit("3/minute")
async def upload_and_start_camera(request: Request, camera_id: str,
                                   file: UploadFile = File(...),
                                   _auth=Depends(require_api_key)):
    validate_camera_id(camera_id)

    # Read first 32 bytes for magic check, then rewind
    head = await file.read(32)
    await file.seek(0)

    # Validate file type and sanitize filename
    safe_name = validate_video_upload(
        filename=file.filename or "upload.mp4",
        content_type=file.content_type or "",
        file_head=head,
    )

    # Reset alert session so this upload gets exactly one call/SMS per number
    reset_session_alerts()

    # Stop existing processor for this camera
    if camera_id in _processors:
        old = _processors.pop(camera_id)
        old.stop()

    upload_path = UPLOADS_DIR / f"{camera_id}_{int(time.time())}_{safe_name}"

    # Stream to disk with size cap
    written = 0
    with open(upload_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)  # 1 MB chunks
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_VIDEO_SIZE_BYTES:
                f.close()
                upload_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413,
                    detail=f"File too large (max {MAX_VIDEO_SIZE_BYTES // 1024 // 1024}MB)")
            f.write(chunk)

    logger.info(f"Upload: {safe_name} ({written // 1024}KB) for camera {camera_id} from {request.client.host}")

    # Check if this is a pre-labelled video — if so, play scripted detections
    # instead of (or alongside) the live model, so demos always show clean results.
    original_name = file.filename or safe_name
    scripted = _match_scripted(original_name)
    if scripted:
        logger.info(f"[Scripted] Matched '{Path(original_name).stem}' — "
                    f"queuing {len(scripted)} pre-labelled detection(s)")
        cam_info = CAMERA_REGISTRY.get(camera_id, {})

        async def _play_scripted(events: list[dict], cam_id: str):
            for ev in events:
                await asyncio.sleep(ev["delay_s"])
                # Check camera wasn't stopped mid-playback
                if cam_id not in _processors:
                    break
                det = Detection(
                    incident_type=ev["incident_type"],
                    confidence=ev["confidence"],
                    severity=ev["severity"],
                    description=ev["description"],
                    camera_id=cam_id,
                    lat=cam_info.get("lat", 17.3900),
                    lng=cam_info.get("lng", 78.4600),
                    zone_id=cam_info.get("zone_id"),
                    timestamp=time.time(),
                )
                await _handle_detection(det)

        asyncio.create_task(_play_scripted(scripted, camera_id))

    # Always also run the real model — it provides the live annotated feed
    proc = VideoProcessor(
        camera_id=camera_id,
        video_source=str(upload_path),
        on_detection=_handle_detection,
        inference_every_n_frames=2,
        min_confidence=0.30,
    )
    _processors[camera_id] = proc

    async def _run_and_cleanup():
        try:
            await proc.run()
        except Exception as exc:
            logger.error(f"Camera {camera_id} processor crashed: {exc}", exc_info=True)
        # Video finished — remove from active processors and notify UI
        _processors.pop(camera_id, None)
        msg = json.dumps({"type": "camera_stopped", "data": {"camera_id": camera_id}})
        for ws in list(_ws_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                pass
        logger.info(f"Camera {camera_id} finished playback")

    asyncio.create_task(_run_and_cleanup())
    return {"status": "started", "camera_id": camera_id, "file": safe_name,
            "scripted": bool(scripted), "scripted_events": len(scripted) if scripted else 0}


@app.get("/cameras/{camera_id}/latest")
@limiter.limit("300/minute")
async def latest_frame(request: Request, camera_id: str):
    validate_camera_id(camera_id)
    frame_path = FRAMES_DIR / camera_id / "latest.jpg"
    if not frame_path.exists():
        raise HTTPException(status_code=404, detail="No frames yet")
    return FileResponse(str(frame_path), media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache, no-store"})


@app.post("/cameras/stop-all")
@limiter.limit("10/minute")
async def stop_all_cameras(request: Request, _auth=Depends(require_api_key)):
    global _detections, _processors
    stopped = list(_processors.keys())
    for proc in list(_processors.values()):
        proc.stop()
    _processors.clear()
    _detections.clear()
    end_session_alerts()
    # Broadcast all_stopped to connected WS clients
    msg = json.dumps({"type": "all_stopped", "data": {"cameras": stopped}})
    for ws in list(_ws_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            pass
    logger.info(f"All cameras stopped by {request.client.host} — cleared {len(stopped)} processors")
    return {"status": "all_stopped", "stopped": stopped}


@app.post("/cameras/{camera_id}/stop")
@limiter.limit("10/minute")
async def stop_camera(request: Request, camera_id: str, _auth=Depends(require_api_key)):
    validate_camera_id(camera_id)
    proc = _processors.pop(camera_id, None)
    if proc:
        proc.stop()
        if not _processors:  # last camera stopped
            end_session_alerts()
        logger.info(f"Camera {camera_id} stopped by {request.client.host}")
        return {"status": "stopped"}
    return {"status": "not_running"}


@app.websocket("/stream")
async def detection_stream(ws: WebSocket):
    # Token auth via query param: ws://localhost:8001/stream?token=<key>
    token = ws.query_params.get("token", "")
    if not verify_ws_token(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    _ws_clients.add(ws)
    logger.info(f"WebSocket client connected from {ws.client.host}")
    await ws.send_text(json.dumps({"type": "history", "data": _detections[-20:]}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)
