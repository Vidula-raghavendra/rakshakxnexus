"""
Rakshak → NEXUS sensor adapter.

Rakshak is the CCTV ML incident detection model (YOLOv8-based road incident detection).
Output format: {incident_type, lat, lng, confidence, timestamp, camera_id}

Two modes:
  - replay: replays the recorded Sept 2024 Mehdipatnam incident sequence
  - live:   polls a running Rakshak API endpoint

Emits NEXUS CityEvent objects that feed into the governor's perception.
"""
import asyncio
import os
import time
import json
import aiohttp
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass
from enum import Enum

from ..alerts.twilio_alerts import dispatch_alerts


class IncidentType(str, Enum):
    ROAD_FLOOD            = "road_flood"
    VEHICLE_STRANDED      = "vehicle_stranded"
    CROWD_SURGE           = "crowd_surge"
    ROAD_BLOCKED          = "road_blocked"
    ROAD_ACCIDENT         = "road_accident"
    ACCIDENT              = "accident"
    POWER_LINE_DOWN       = "power_line_down"
    TRAFFIC_SIGNAL_ISSUE  = "traffic_signal_issue"
    FIGHT_VIOLENCE        = "fight_violence"
    WOMEN_SAFETY          = "women_safety"
    ANIMAL_ON_ROAD        = "animal_on_road"
    ABANDONED_OBJECT      = "abandoned_object"
    GARBAGE_DUMPING       = "garbage_dumping"
    BUILDING_DAMAGE       = "building_damage"


@dataclass
class RakshakIncident:
    incident_type: IncidentType
    lat: float
    lng: float
    confidence: float       # 0.0–1.0
    camera_id: str
    timestamp: float
    severity: str           # low | medium | high | critical
    description: str
    zone_id: Optional[str] = None  # mapped NEXUS zone


# Recorded incident sequence from Sept 2024 Hyderabad floods
# Each entry: (seconds_into_scenario, incident_data)
SEPT_2024_REPLAY = [
    (30,  RakshakIncident(IncidentType.ROAD_FLOOD,       17.3957, 78.4290, 0.94, "cam_meh_001", 0, "critical", "Water rising at Mehdipatnam underpass — vehicles entering", "mehdipatnam_up")),
    (60,  RakshakIncident(IncidentType.VEHICLE_STRANDED, 17.3955, 78.4288, 0.89, "cam_meh_002", 0, "critical", "2 vehicles stalled in floodwater, occupants visible", "mehdipatnam_up")),
    (90,  RakshakIncident(IncidentType.ROAD_BLOCKED,     17.4052, 78.4107, 0.91, "cam_tol_001", 0, "high",     "Tolichowki underpass entry — water at bumper level", "tolichowki_up")),
    (120, RakshakIncident(IncidentType.CROWD_SURGE,      17.3960, 78.4295, 0.82, "cam_meh_003", 0, "high",     "Pedestrians wading through flood, crowd forming", "mehdipatnam_up")),
    (150, RakshakIncident(IncidentType.VEHICLE_STRANDED, 17.4050, 78.4105, 0.95, "cam_tol_002", 0, "critical", "Bus stalled mid-underpass, passengers on roof", "tolichowki_up")),
    (180, RakshakIncident(IncidentType.ROAD_FLOOD,       17.3921, 78.4867, 0.78, "cam_nar_001", 0, "high", "Narayanguda underpass water level rising rapidly", "narayanguda_up")),
    (240, RakshakIncident(IncidentType.POWER_LINE_DOWN,  17.3960, 78.4310, 0.87, "cam_pow_001", 0, "critical", "Power line arcing near Mehdipatnam substation", "mehdipatnam_up")),
    (300, RakshakIncident(IncidentType.ROAD_BLOCKED,     17.3700, 78.5000, 0.92, "cam_mal_001", 0, "high",     "Malakpet underpass completely submerged", "malakpet_up")),
    (360, RakshakIncident(IncidentType.ACCIDENT,         17.3815, 78.4780, 0.88, "cam_osman_001", 0, "medium",  "RTA near Osmania Hospital — access road partly blocked", "mehdipatnam_up")),
    (420, RakshakIncident(IncidentType.ROAD_FLOOD,       17.3468, 78.5521, 0.90, "cam_lb_001",   0, "high",     "LB Nagar underpass flooding — traffic backing up", "lb_nagar_up")),
]


class RakshakAdapter:
    def __init__(self, simulation_engine):
        self.engine = simulation_engine
        self.mode = os.getenv("RAKSHAK_MODE", "replay")
        self.api_url = os.getenv("RAKSHAK_API_URL", "http://localhost:8001/detections")
        self._on_incident: Optional[Callable[[RakshakIncident], Awaitable[None]]] = None
        self._running = False
        self._scenario_start: Optional[float] = None
        self._replay_index = 0

    def on_incident(self, callback: Callable[[RakshakIncident], Awaitable[None]]):
        self._on_incident = callback

    async def start(self):
        self._running = True
        if self.mode == "replay":
            await self._run_replay()
        else:
            await self._run_live()

    def start_scenario(self):
        """Called when flood scenario begins."""
        self._scenario_start = time.time()
        self._replay_index = 0

    def stop(self):
        self._running = False

    async def _run_replay(self):
        """Replay recorded incidents in scenario-relative time."""
        while self._running:
            if self._scenario_start is None:
                await asyncio.sleep(1)
                continue

            elapsed = time.time() - self._scenario_start
            # Check if next incident should fire
            while self._replay_index < len(SEPT_2024_REPLAY):
                trigger_time, incident = SEPT_2024_REPLAY[self._replay_index]
                if elapsed >= trigger_time:
                    incident.timestamp = time.time()
                    await self._handle_incident(incident)
                    self._replay_index += 1
                else:
                    break

            await asyncio.sleep(0.5)

    async def _run_live(self):
        """Poll real Rakshak endpoint for new detections."""
        import os as _os
        api_key = _os.getenv("NEXUS_API_KEY", "")
        headers = {"X-API-Key": api_key} if api_key else {}
        last_ts = 0
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    async with session.get(
                        self.api_url,
                        params={"since": last_ts},
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for raw in data.get("detections", []):
                                incident = self._parse_raw(raw)
                                if incident:
                                    await self._handle_incident(incident)
                                    last_ts = max(last_ts, raw.get("timestamp", 0))
                        elif resp.status == 401:
                            print(f"[Rakshak] Auth failed — check NEXUS_API_KEY in .env.local")
                except Exception as e:
                    print(f"[Rakshak] Live poll error: {e}")
                await asyncio.sleep(2)

    async def _handle_incident(self, incident: RakshakIncident):
        """Feed incident into NEXUS city state and notify callback."""
        state = self.engine.state

        # Update zone congestion / status based on incident
        if incident.zone_id and incident.zone_id in state.zones:
            zone = state.zones[incident.zone_id]
            itype_str = incident.incident_type.value if hasattr(incident.incident_type, 'value') else str(incident.incident_type)
            if itype_str in ("road_flood", "road_blocked", "vehicle_stranded", "accident", "road_accident", "crowd_surge"):
                state.edge_congestion[incident.zone_id] = min(1.0,
                    state.edge_congestion.get(incident.zone_id, 0) + 0.25)
            if incident.severity == "critical":
                from ..core.city_state import ZoneStatus
                zone.status = ZoneStatus.CRITICAL

        # Write into recent_rakshak_incidents so governor perception includes it
        incident_dict = {
            "incident_type": incident.incident_type.value if hasattr(incident.incident_type, 'value') else str(incident.incident_type),
            "camera_id": incident.camera_id,
            "zone_id": incident.zone_id,
            "severity": incident.severity,
            "confidence": round(incident.confidence, 2),
            "description": incident.description,
            "timestamp": incident.timestamp,
        }
        state.recent_rakshak_incidents.append(incident_dict)
        # Keep only last 5 minutes of incidents
        cutoff = time.time() - 300
        state.recent_rakshak_incidents = [
            i for i in state.recent_rakshak_incidents if i["timestamp"] > cutoff
        ]

        # Log as cascade event if critical
        if incident.severity in ("critical", "high"):
            if not isinstance(state.cascade_chain, list):
                state.cascade_chain = []
            state.cascade_chain.append({
                "type": f"rakshak_{incident.incident_type.value}",
                "severity": incident.severity,
                "camera": incident.camera_id,
                "description": incident.description,
                "lat": incident.lat,
                "lng": incident.lng,
                "confidence": incident.confidence,
                "timestamp": incident.timestamp,
            })

        if self._on_incident:
            await self._on_incident(incident)

        # Dispatch emergency alerts for high/critical incidents
        if incident.severity in ("high", "critical"):
            try:
                await dispatch_alerts(incident_dict)
            except Exception as e:
                print(f"[Rakshak] Alert dispatch failed: {e}")

    def _parse_raw(self, raw: dict) -> Optional[RakshakIncident]:
        """Parse a live Rakshak API response into a RakshakIncident."""
        try:
            raw_type = raw.get("incident_type", "road_blocked")
            try:
                itype = IncidentType(raw_type)
            except ValueError:
                # Unknown type — wrap it as a string-compatible enum value
                itype = raw_type  # type: ignore[assignment]
            raw_ts = float(raw.get("timestamp", 0))
            # If the detection timestamp is older than 4 minutes, freshen it
            # so the governor's 5-min TTL filter doesn't immediately prune it
            ts = raw_ts if (time.time() - raw_ts) < 240 else time.time()
            return RakshakIncident(
                incident_type=itype,
                lat=float(raw["lat"]),
                lng=float(raw["lng"]),
                confidence=float(raw.get("confidence", 0.8)),
                camera_id=raw.get("camera_id", "unknown"),
                timestamp=ts,
                severity=raw.get("severity", "medium"),
                description=raw.get("description", ""),
                zone_id=raw.get("zone_id"),
            )
        except Exception as e:
            print(f"[Rakshak] Failed to parse detection: {e} | raw={raw}")
            return None
