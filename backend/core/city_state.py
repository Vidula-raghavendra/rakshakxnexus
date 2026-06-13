"""
Live city state — the single source of truth that the simulation engine
writes and the AI governor reads every tick.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class ZoneStatus(str, Enum):
    NORMAL = "normal"
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"
    EVACUATING = "evacuating"


class ResourceType(str, Enum):
    AMBULANCE = "ambulance"
    FIRE_TRUCK = "fire_truck"
    POLICE = "police"
    POWER_CREW = "power_crew"


@dataclass
class SensorReading:
    zone_id: str
    rainfall_mm_per_hour: float
    water_level_m: float
    timestamp: float


@dataclass
class Resource:
    id: str
    type: ResourceType
    lat: float
    lng: float
    status: str = "available"  # available | dispatched | en_route
    assigned_to: Optional[str] = None


@dataclass
class ZoneState:
    id: str
    name: str
    lat: float
    lng: float
    status: ZoneStatus = ZoneStatus.NORMAL
    rainfall_mm_per_hour: float = 0.0
    water_level_m: float = 0.0
    is_flooded: bool = False
    roads_blocked: List[str] = field(default_factory=list)
    evacuation_progress: float = 0.0  # 0.0–1.0


@dataclass
class SubstationState:
    id: str
    name: str
    lat: float
    lng: float
    online: bool = True
    load_mw: float = 0.0
    max_load_mw: float = 120.0
    flood_risk: bool = False
    overloaded: bool = False


@dataclass
class HospitalState:
    id: str
    name: str
    lat: float
    lng: float
    has_power: bool = True
    backup_power_active: bool = False
    accessible: bool = True
    capacity_used: float = 0.0


@dataclass
class CityState:
    tick: int = 0
    simulation_time: float = 0.0  # seconds since scenario start
    scenario_active: str = "normal"  # normal | flood_sept2024

    # Sensor readings per flood zone
    sensor_readings: Dict[str, SensorReading] = field(default_factory=dict)

    # Zone statuses
    zones: Dict[str, ZoneState] = field(default_factory=dict)

    # Infrastructure
    substations: Dict[str, SubstationState] = field(default_factory=dict)
    hospitals: Dict[str, HospitalState] = field(default_factory=dict)

    # Resources
    resources: Dict[str, Resource] = field(default_factory=dict)

    # Traffic: edge_id -> congestion 0.0–1.0
    edge_congestion: Dict[str, float] = field(default_factory=dict)

    # Blocked edges (flooded roads)
    blocked_edges: List[str] = field(default_factory=list)

    # AI decisions log
    recent_decisions: List[dict] = field(default_factory=list)

    # Cascade failure chain
    cascade_chain: List[dict] = field(default_factory=list)

    # Vehicle simulation snapshot (updated each tick)
    vehicles: List[dict] = field(default_factory=list)
    footfall: Dict[str, int] = field(default_factory=dict)

    # CCTV detections from Rakshak (last 5 minutes)
    recent_rakshak_incidents: List[dict] = field(default_factory=list)

    def to_snapshot(self) -> dict:
        """Serialisable snapshot for WebSocket broadcast."""
        return {
            "tick": self.tick,
            "simulation_time": self.simulation_time,
            "scenario_active": self.scenario_active,
            "zones": {k: _zone_dict(v) for k, v in self.zones.items()},
            "substations": {k: _sub_dict(v) for k, v in self.substations.items()},
            "hospitals": {k: _hosp_dict(v) for k, v in self.hospitals.items()},
            "resources": {k: _res_dict(v) for k, v in self.resources.items()},
            "sensor_readings": {k: _sensor_dict(v) for k, v in self.sensor_readings.items()},
            "edge_congestion": self.edge_congestion,
            "blocked_edges": self.blocked_edges,
            "recent_decisions": self.recent_decisions[-20:],
            "cascade_chain": self.cascade_chain,
            "vehicles": self.vehicles,
            "footfall": self.footfall,
        }


def _zone_dict(z: ZoneState) -> dict:
    return {"id": z.id, "name": z.name, "lat": z.lat, "lng": z.lng,
            "status": z.status.value, "rainfall_mm_per_hour": z.rainfall_mm_per_hour,
            "water_level_m": z.water_level_m, "is_flooded": z.is_flooded,
            "roads_blocked": z.roads_blocked, "evacuation_progress": z.evacuation_progress}

def _sub_dict(s: SubstationState) -> dict:
    return {"id": s.id, "name": s.name, "lat": s.lat, "lng": s.lng,
            "online": s.online, "load_mw": s.load_mw, "max_load_mw": s.max_load_mw,
            "flood_risk": s.flood_risk, "overloaded": s.overloaded}

def _hosp_dict(h: HospitalState) -> dict:
    return {"id": h.id, "name": h.name, "lat": h.lat, "lng": h.lng,
            "has_power": h.has_power, "backup_power_active": h.backup_power_active,
            "accessible": h.accessible, "capacity_used": h.capacity_used}

def _res_dict(r: Resource) -> dict:
    return {"id": r.id, "type": r.type.value, "lat": r.lat, "lng": r.lng,
            "status": r.status, "assigned_to": r.assigned_to}

def _sensor_dict(s: SensorReading) -> dict:
    return {"zone_id": s.zone_id, "rainfall_mm_per_hour": s.rainfall_mm_per_hour,
            "water_level_m": s.water_level_m, "timestamp": s.timestamp}
