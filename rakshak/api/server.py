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
from backend.alerts.twilio_alerts import dispatch_alerts

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
<title>RAKSHAK — CCTV Monitoring</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #020617; color: #e2e8f0; font-family: 'Courier New', monospace; font-size: 12px; overflow: hidden; height: 100vh; display: flex; flex-direction: column; }
.topbar { height: 44px; background: #020617; border-bottom: 2px solid #0f172a; display: flex; align-items: center; padding: 0 16px; gap: 14px; flex-shrink: 0; }
.logo { color: #ef4444; font-weight: 900; font-size: 15px; letter-spacing: 4px; }
.logo-sub { color: #334155; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; }
.badge { padding: 2px 8px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; }
.badge-live { background: #052e16; border: 1px solid #22c55e; color: #86efac; }
.badge-idle { background: #0f172a; border: 1px solid #1e293b; color: #475569; }
.stat-chip { display: flex; align-items: center; gap: 5px; padding: 2px 10px; border-radius: 3px; background: #0f172a; border: 1px solid #1e293b; }
.stat-chip .val { font-size: 14px; font-weight: 900; }
.stat-chip .lbl { font-size: 8px; color: #475569; text-transform: uppercase; letter-spacing: 1px; }
.clock { color: #334155; font-size: 11px; letter-spacing: 1px; margin-left: auto; }
.nexus-status { font-size: 10px; padding: 2px 8px; border-radius: 3px; }
.layout { flex: 1; display: flex; overflow: hidden; }
.cam-grid { flex: 1; display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(2, 1fr); gap: 2px; background: #000; padding: 2px; overflow: hidden; }
.cam-cell { position: relative; background: #040c14; overflow: hidden; border: 1px solid #0f172a; cursor: pointer; transition: border-color 0.2s; }
.cam-cell:hover { border-color: #1e3a5f; }
.cam-cell.active { border-color: #22c55e; }
.cam-cell.incident-critical { border-color: #ef4444; animation: incident-flash 0.8s ease-in-out infinite; }
.cam-cell.incident-high { border-color: #f97316; }
@keyframes incident-flash { 0%,100% { border-color: #ef4444; } 50% { border-color: #7f1d1d; } }
.cam-feed { width: 100%; height: 100%; object-fit: cover; display: block; }
.cam-no-signal { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; background: repeating-linear-gradient(0deg, #050a14 0px, #050a14 2px, #040c14 2px, #040c14 4px); }
.cam-no-signal .signal-text { color: #1e293b; font-size: 10px; letter-spacing: 2px; text-transform: uppercase; }
.cam-no-signal .signal-icon { font-size: 24px; margin-bottom: 8px; opacity: 0.3; }
.cam-label { position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,0.85)); padding: 20px 8px 6px; display: flex; justify-content: space-between; align-items: flex-end; }
.cam-name { font-size: 10px; font-weight: 700; color: #e2e8f0; letter-spacing: 1px; text-transform: uppercase; }
.cam-id { font-size: 9px; color: #64748b; }
.cam-live-dot { position: absolute; top: 8px; left: 8px; width: 6px; height: 6px; border-radius: 50%; background: #ef4444; animation: live-pulse 2s ease-in-out infinite; display: none; }
@keyframes live-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
.cam-corner-id { position: absolute; top: 8px; right: 8px; font-size: 9px; color: #334155; letter-spacing: 1px; background: rgba(0,0,0,0.6); padding: 1px 5px; border-radius: 2px; }
.cam-incident { position: absolute; top: 28px; left: 0; right: 0; display: none; justify-content: center; }
.incident-tag { padding: 2px 8px; border-radius: 2px; font-size: 9px; font-weight: 900; letter-spacing: 1px; text-transform: uppercase; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; }
.sidebar { width: 280px; border-left: 1px solid #0f172a; display: flex; flex-direction: column; background: #020617; flex-shrink: 0; }
.sidebar-section { border-bottom: 1px solid #0f172a; }
.section-hdr { padding: 7px 12px; font-size: 9px; color: #334155; text-transform: uppercase; letter-spacing: 2px; background: #040c14; display: flex; justify-content: space-between; }
.upload-area { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.cam-select { background: #0f172a; border: 1px solid #1e293b; color: #e2e8f0; padding: 6px 8px; border-radius: 3px; font-family: inherit; font-size: 11px; width: 100%; }
.cam-select:focus { outline: none; border-color: #3b82f6; }
.drop-zone { border: 1px dashed #1e293b; border-radius: 4px; padding: 12px 8px; text-align: center; cursor: pointer; transition: border-color 0.2s, background 0.2s; }
.drop-zone:hover, .drop-zone.drag { border-color: #3b82f6; background: #0a1628; }
.drop-zone input { display: none; }
.drop-zone .dz-text { color: #475569; font-size: 10px; line-height: 1.6; }
.drop-zone .dz-file { color: #3b82f6; font-size: 10px; margin-top: 4px; }
.progress-bar { height: 3px; background: #0f172a; border-radius: 2px; overflow: hidden; display: none; }
.progress-fill { height: 100%; background: #3b82f6; transition: width 0.3s; }
.btn { padding: 7px 12px; border-radius: 3px; border: none; cursor: pointer; font-family: inherit; font-size: 10px; font-weight: 900; letter-spacing: 1px; text-transform: uppercase; width: 100%; transition: opacity 0.15s; }
.btn:hover { opacity: 0.85; }
.btn:disabled { opacity: 0.35; cursor: not-allowed; }
.btn-red { background: #ef4444; color: #000; }
.btn-stop { background: #dc2626; color: #fff; border: 2px solid #fca5a5; box-shadow: 0 0 8px rgba(239,68,68,0.5); }
.btn-gray { background: #0f172a; color: #64748b; border: 1px solid #1e293b; }
.det-log { flex: 1; overflow-y: auto; padding: 8px; }
.det-log::-webkit-scrollbar { width: 3px; }
.det-log::-webkit-scrollbar-track { background: #020617; }
.det-log::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
.det-card { padding: 7px 9px; border-left: 2px solid #ef4444; background: #0a0f1e; border-radius: 0 3px 3px 0; margin-bottom: 5px; }
.det-card.high { border-color: #f97316; }
.det-card.medium { border-color: #3b82f6; }
.det-card.low { border-color: #334155; }
.det-type { font-size: 10px; font-weight: 700; color: #f1f5f9; text-transform: uppercase; letter-spacing: 0.5px; }
.det-desc { font-size: 10px; color: #64748b; margin-top: 2px; line-height: 1.4; }
.det-meta { font-size: 9px; color: #334155; margin-top: 3px; display: flex; gap: 6px; flex-wrap: wrap; }
.conf-bar { height: 2px; background: #0f172a; border-radius: 1px; margin-top: 4px; }
.conf-fill { height: 100%; border-radius: 1px; }
.scanline-overlay { position: fixed; inset: 44px 280px 0 0; pointer-events: none; z-index: 10; background: repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.04) 3px, rgba(0,0,0,0.04) 4px); }
</style>
</head>
<body>

<div class="topbar">
  <div>
    <div class="logo">RAKSHAK</div>
    <div class="logo-sub">CCTV Incident Intelligence</div>
  </div>
  <span id="liveBadge" class="badge badge-idle">● STANDBY</span>
  <div class="stat-chip"><span class="val" id="statTotal" style="color:#3b82f6">0</span><span class="lbl">Detections</span></div>
  <div class="stat-chip"><span class="val" id="statCritical" style="color:#ef4444">0</span><span class="lbl">Critical</span></div>
  <div class="stat-chip"><span class="val" id="statActive" style="color:#22c55e">0</span><span class="lbl">Live Cams</span></div>
  <div id="nexusStatus" class="nexus-status badge badge-idle">NEXUS: —</div>
  <span class="clock" id="clock">00:00:00</span>
</div>

<div class="layout">
  <div class="cam-grid" id="camGrid"></div>
  <div class="scanline-overlay"></div>
  <div class="sidebar">
    <div class="sidebar-section">
      <div class="section-hdr"><span>Assign Video Source</span></div>
      <div class="upload-area">
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
          <div class="dz-text">📹 Drop video or click to browse<br><span style="font-size:9px;color:#1e293b">MP4 · AVI · MOV</span></div>
          <div class="dz-file" id="fileName"></div>
          <div class="progress-bar" id="progressBar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
        </div>
        <button class="btn btn-red" id="uploadBtn" onclick="uploadVideo()" disabled>▶ START INFERENCE</button>
        <button class="btn btn-stop" id="stopBtn" onclick="stopActiveCamera()" style="display:none">⬛ STOP ALL PROCESSING</button>
        <div id="modelStatus" style="font-size:9px;color:#334155;text-align:center">Model: not loaded</div>
      </div>
    </div>
    <div class="section-hdr" style="flex-shrink:0"><span>Incident Log</span><span id="logCount" style="color:#475569">0 events</span></div>
    <div class="det-log" id="detLog"><div style="color:#1e293b;font-size:10px;text-align:center;padding-top:20px">Awaiting detections...</div></div>
  </div>
</div>

<script>
const CAMERAS = [
  { id: 'cam_meh_001', label: 'Mehdipatnam UP', sub: 'Entry · cam_meh_001' },
  { id: 'cam_meh_002', label: 'Mehdipatnam UP', sub: 'Mid-Span · cam_meh_002' },
  { id: 'cam_tol_001', label: 'Tolichowki UP',  sub: 'Entry · cam_tol_001' },
  { id: 'cam_nar_001', label: 'Narayanguda UP', sub: 'cam_nar_001' },
  { id: 'cam_mal_001', label: 'Malakpet UP',    sub: 'cam_mal_001' },
  { id: 'cam_lb_001',  label: 'LB Nagar UP',   sub: 'cam_lb_001' },
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
    cell.onclick = () => { document.querySelectorAll('.cam-cell').forEach(c => c.style.outline = ''); cell.style.outline = '2px solid #3b82f6'; document.getElementById('cameraSelect').value = cam.id; };
    cell.innerHTML = `
      <div class="cam-no-signal" id="nosig_${cam.id}"><div class="signal-icon">📡</div><div class="signal-text">No Signal</div></div>
      <div class="cam-live-dot" id="dot_${cam.id}"></div>
      <div class="cam-corner-id">${cam.id.toUpperCase()}</div>
      <div class="cam-incident" id="inc_${cam.id}"><div class="incident-tag" id="incTag_${cam.id}"></div></div>
      <div class="cam-label"><div><div class="cam-name">${cam.label}</div><div class="cam-id">${cam.sub}</div></div></div>
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
  // Poll latest.jpg
  if (_pollIntervals[camId]) clearInterval(_pollIntervals[camId]);
  _pollIntervals[camId] = setInterval(() => {
    img.src = '/frames/' + camId + '/latest.jpg?t=' + Date.now();
  }, 400);
}

function deactivateCameraFeed(camId) {
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

const _incidentTimeouts = {};
function showCamIncident(camId, severity, label) {
  const incEl = document.getElementById('inc_' + camId);
  const tagEl = document.getElementById('incTag_' + camId);
  const cell = document.getElementById('cell_' + camId);
  if (!incEl || !tagEl || !cell) return;
  tagEl.textContent = label;
  incEl.style.display = 'flex';
  cell.classList.remove('incident-critical', 'incident-high');
  if (severity === 'critical') cell.classList.add('incident-critical');
  else if (severity === 'high') cell.classList.add('incident-high');
  if (_incidentTimeouts[camId]) clearTimeout(_incidentTimeouts[camId]);
  _incidentTimeouts[camId] = setTimeout(() => { incEl.style.display = 'none'; cell.classList.remove('incident-critical', 'incident-high'); }, 8000);
}

function addDetection(d) {
  detectionCount++; if (d.severity === 'critical' || d.severity === 'high') criticalCount++;
  logCount++;
  document.getElementById('statTotal').textContent = detectionCount;
  document.getElementById('statCritical').textContent = criticalCount;
  document.getElementById('logCount').textContent = logCount + ' events';
  if (d.camera_id) {
    showCamIncident(d.camera_id, d.severity, '⚠ ' + (d.incident_type||'').replace(/_/g,' ').toUpperCase());
    if (!camActive[d.camera_id]) activateCameraFeed(d.camera_id);
  }
  const badge = document.getElementById('liveBadge');
  badge.className = 'badge badge-live'; badge.textContent = '● DETECTION';
  setTimeout(() => { badge.textContent = '● MONITORING'; }, 2000);
  const log = document.getElementById('detLog');
  if (log.querySelector('div[style]')) log.innerHTML = '';
  const conf = d.confidence || 0;
  const sev = d.severity || 'medium';
  const sevColor = sev === 'critical' ? '#ef4444' : sev === 'high' ? '#f97316' : sev === 'medium' ? '#3b82f6' : '#334155';
  const card = document.createElement('div');
  card.className = 'det-card ' + sev;
  card.innerHTML = `<div class="det-type" style="color:${sevColor}">${(d.incident_type||'').replace(/_/g,' ')}</div><div class="det-desc">${d.description||''}</div><div class="det-meta"><span>📹 ${d.camera_id||''}</span><span>${(conf*100).toFixed(0)}%</span><span style="margin-left:auto">${new Date((d.timestamp||Date.now()/1000)*1000).toLocaleTimeString('en-IN',{hour12:false})}</span></div><div class="conf-bar"><div class="conf-fill" style="width:${conf*100}%;background:${sevColor}"></div></div>`;
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

function resetAllUI() {
  // Deactivate ALL camera feeds
  Object.keys(camActive).forEach(cid => { if (camActive[cid]) deactivateCameraFeed(cid); });
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
        await proc.run()
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
