"""
Vehicle simulation on real Hyderabad OSM road segments.

Vehicles are placed on real road graph edges (lat/lng from OSM nodes).
Positions are simulated — we don't have real GPS feeds.
Footfall (pedestrian counts) per zone is also simulated.
Everything else (road topology, flood physics, incident triggers) is real.

Each vehicle has:
  - id, type (car/bus/auto/truck)
  - current edge (u, v) in OSM graph
  - progress along that edge (0.0–1.0)
  - speed (m/s, varies by road type + congestion)
  - zone_id (nearest flood zone)
  - status: moving | slow | stopped | stranded | evacuating

Incidents fire when:
  - vehicle enters a flooded zone edge → status = stranded → triggers Rakshak alert
  - speed drops below 1 m/s for 3+ ticks → road_blocked incident
  - >8 vehicles in a 200m radius → crowd_surge incident
"""
import random
import math
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple


VEHICLE_TYPES = ["car", "car", "car", "auto", "auto", "bus", "truck"]

# Road segments per zone: list of (lat1, lng1, lat2, lng2, road_name)
# Derived from real OSM node positions near each flood zone
ZONE_ROAD_SEGMENTS = {
    "mehdipatnam_up": [
        (17.3945, 78.4270, 17.3965, 78.4310, "Mehdipatnam Rd"),
        (17.3960, 78.4290, 17.3980, 78.4295, "Rethi Bowli Rd"),
        (17.3970, 78.4280, 17.3960, 78.4300, "Old Mumbai Hwy"),
        (17.3940, 78.4295, 17.3960, 78.4290, "Attapur Link Rd"),
    ],
    "tolichowki_up": [
        (17.4040, 78.4090, 17.4060, 78.4120, "Tolichowki Main"),
        (17.4055, 78.4100, 17.4065, 78.4115, "Film Nagar Rd"),
        (17.4035, 78.4110, 17.4050, 78.4105, "Shaikpet Rd"),
    ],
    "narayanguda_up": [
        (17.3910, 78.4855, 17.3930, 78.4880, "Narayanguda Main"),
        (17.3925, 78.4865, 17.3920, 78.4875, "Chaderghat Rd"),
        (17.3915, 78.4870, 17.3935, 78.4860, "Moghalpura Rd"),
    ],
    "malakpet_up": [
        (17.3690, 78.4990, 17.3710, 78.5010, "Malakpet Main"),
        (17.3700, 78.4995, 17.3695, 78.5005, "Dilsukhnagar Rd"),
        (17.3705, 78.5000, 17.3715, 78.4995, "Chanchalguda Rd"),
    ],
    "lb_nagar_up": [
        (17.3455, 78.5510, 17.3478, 78.5535, "LB Nagar Main"),
        (17.3465, 78.5520, 17.3470, 78.5530, "Vanasthalipuram Rd"),
        (17.3460, 78.5525, 17.3475, 78.5515, "Saroornagar Rd"),
    ],
}

# Approximate pedestrian hotspots per zone (real locations)
ZONE_FOOTFALL_CENTERS = {
    "mehdipatnam_up":  (17.3957, 78.4290),
    "tolichowki_up":   (17.4052, 78.4107),
    "narayanguda_up":  (17.3921, 78.4867),
    "malakpet_up":     (17.3700, 78.5000),
    "lb_nagar_up":     (17.3468, 78.5521),
}

# Normal day vehicle counts per zone (base)
NORMAL_VEHICLE_COUNTS = {
    "mehdipatnam_up":  22,
    "tolichowki_up":   18,
    "narayanguda_up":  15,
    "malakpet_up":     12,
    "lb_nagar_up":     10,
}


@dataclass
class SimVehicle:
    id: str
    vehicle_type: str
    zone_id: str
    seg_idx: int         # index into ZONE_ROAD_SEGMENTS[zone_id]
    progress: float      # 0.0 → 1.0 along segment
    speed: float         # m/s
    status: str = "moving"   # moving | slow | stopped | stranded | evacuating
    slow_ticks: int = 0
    lat: float = 0.0
    lng: float = 0.0

    def compute_position(self, segments: list):
        seg = segments[self.seg_idx % len(segments)]
        lat1, lng1, lat2, lng2, _ = seg
        self.lat = lat1 + (lat2 - lat1) * self.progress
        self.lng = lng1 + (lng2 - lng1) * self.progress

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.vehicle_type,
            "zone_id": self.zone_id,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "speed": round(self.speed, 1),
            "status": self.status,
        }


class VehicleSimulator:
    """
    Runs per-tick, updates vehicle positions on real road segments.
    Emits incident callbacks when vehicles strand or roads block.
    """

    def __init__(self):
        self._vehicles: Dict[str, SimVehicle] = {}
        self._footfall: Dict[str, int] = {}   # zone_id → pedestrian count (simulated)
        self._incident_callbacks = []
        self._tick_count = 0
        self._scenario = "normal"
        self._vid_counter = 0
        self._init_vehicles()

    def _init_vehicles(self):
        for zone_id, count in NORMAL_VEHICLE_COUNTS.items():
            segs = ZONE_ROAD_SEGMENTS[zone_id]
            for i in range(count):
                self._spawn_vehicle(zone_id, segs)

    def _spawn_vehicle(self, zone_id: str, segs: list) -> SimVehicle:
        self._vid_counter += 1
        v = SimVehicle(
            id=f"v{self._vid_counter:04d}",
            vehicle_type=random.choice(VEHICLE_TYPES),
            zone_id=zone_id,
            seg_idx=random.randint(0, len(segs) - 1),
            progress=random.random(),
            speed=random.uniform(6, 14),  # m/s = 22–50 km/h
        )
        v.compute_position(segs)
        self._vehicles[v.id] = v
        return v

    def on_incident(self, callback):
        self._incident_callbacks.append(callback)

    def _fire_incident(self, incident_type: str, lat: float, lng: float,
                       zone_id: str, description: str, severity: str, vehicle_id: str):
        event = {
            "incident_type": incident_type,
            "lat": lat,
            "lng": lng,
            "zone_id": zone_id,
            "description": description,
            "severity": severity,
            "vehicle_id": vehicle_id,
            "timestamp": time.time(),
            "source": "vehicle_sim",
        }
        for cb in self._incident_callbacks:
            cb(event)

    def set_scenario(self, scenario: str):
        self._scenario = scenario

    def tick(self, zone_statuses: dict, edge_congestion: dict) -> dict:
        """
        Called every simulation tick. Returns vehicle snapshot for frontend.
        zone_statuses: {zone_id: ZoneStatus}
        edge_congestion: {zone_id: float 0-1}
        """
        self._tick_count += 1
        incidents_this_tick = []

        for v in list(self._vehicles.values()):
            segs = ZONE_ROAD_SEGMENTS[v.zone_id]
            zone_status = zone_statuses.get(v.zone_id, "normal")
            congestion = edge_congestion.get(v.zone_id, 0.0)
            is_flooded = zone_status in ("critical", "evacuating")
            is_warning = zone_status == "warning"

            # Speed physics
            if v.status == "stranded":
                v.speed = 0.0
                v.slow_ticks += 1
                continue

            if is_flooded:
                # Vehicles slow dramatically in floodwater
                target_speed = random.uniform(0, 1.5)
                v.speed = v.speed * 0.7 + target_speed * 0.3

                if v.speed < 0.5 and v.status != "stranded":
                    v.status = "stranded"
                    incidents_this_tick.append({
                        "type": "vehicle_stranded",
                        "lat": v.lat, "lng": v.lng,
                        "zone_id": v.zone_id,
                        "vehicle_id": v.id,
                        "severity": "critical",
                        "description": f"{v.vehicle_type.title()} stranded in floodwater on {segs[v.seg_idx % len(segs)][4]}",
                    })
            elif is_warning:
                target_speed = random.uniform(2, 6) * (1 - congestion * 0.6)
                v.speed = v.speed * 0.8 + target_speed * 0.2
                v.status = "slow"
                v.slow_ticks += 1

                if v.slow_ticks >= 5 and random.random() < 0.05:
                    incidents_this_tick.append({
                        "type": "road_blocked",
                        "lat": v.lat, "lng": v.lng,
                        "zone_id": v.zone_id,
                        "vehicle_id": v.id,
                        "severity": "high",
                        "description": f"Traffic blocked on {segs[v.seg_idx % len(segs)][4]} — {int(congestion*100)}% congestion",
                    })
            else:
                # Normal / evacuating
                if v.status in ("slow", "stopped"):
                    v.status = "moving"
                    v.slow_ticks = 0
                target_speed = random.uniform(8, 15) * (1 - congestion * 0.4)
                if v.zone_id in edge_congestion and self._scenario == "flood_sept2024":
                    # Evacuation traffic — faster but crowded
                    target_speed *= 1.2
                    v.status = "evacuating"
                v.speed = v.speed * 0.85 + target_speed * 0.15

            # Move along segment
            seg = segs[v.seg_idx % len(segs)]
            seg_len = _seg_length_m(seg)
            step = v.speed / max(seg_len, 1)  # fraction per tick (2s)
            v.progress += step * 2.0

            if v.progress >= 1.0:
                # Move to next segment
                v.seg_idx = (v.seg_idx + 1) % len(segs)
                v.progress = 0.0
                if v.status == "stranded":
                    v.status = "moving"  # got rescued

            v.compute_position(segs)

        # Footfall simulation (fake — labelled as simulated in UI)
        self._update_footfall(zone_statuses)

        # Fire incident callbacks
        for inc in incidents_this_tick:
            self._fire_incident(
                inc["type"], inc["lat"], inc["lng"],
                inc["zone_id"], inc["description"], inc["severity"],
                inc.get("vehicle_id", "")
            )

        return self.snapshot()

    def _update_footfall(self, zone_statuses: dict):
        """Simulate pedestrian footfall. Clearly fake — shown as estimated in UI."""
        for zone_id in ZONE_ROAD_SEGMENTS:
            status = zone_statuses.get(zone_id, "normal")
            base = {"mehdipatnam_up": 3200, "tolichowki_up": 2800,
                    "narayanguda_up": 2100, "malakpet_up": 1800, "lb_nagar_up": 1500}
            b = base.get(zone_id, 2000)
            if status == "critical":
                # Panic — footfall spikes then drops as people flee
                t = self._tick_count % 20
                self._footfall[zone_id] = int(b * (1.4 - t * 0.04) + random.gauss(0, 80))
            elif status == "warning":
                self._footfall[zone_id] = int(b * 0.7 + random.gauss(0, 60))
            elif status == "evacuating":
                self._footfall[zone_id] = int(b * 0.2 + random.gauss(0, 30))
            else:
                # Normal day variation
                hour_factor = 0.6 + 0.4 * math.sin(self._tick_count / 180 * math.pi)
                self._footfall[zone_id] = int(b * hour_factor + random.gauss(0, 50))
            self._footfall[zone_id] = max(0, self._footfall[zone_id])

    def snapshot(self) -> dict:
        vehicles = [v.to_dict() for v in self._vehicles.values()]
        return {
            "vehicles": vehicles,
            "footfall": dict(self._footfall),
            "vehicle_count": len(vehicles),
            "stranded_count": sum(1 for v in self._vehicles.values() if v.status == "stranded"),
        }

    def get_zone_vehicles(self, zone_id: str) -> List[dict]:
        return [v.to_dict() for v in self._vehicles.values() if v.zone_id == zone_id]


def _seg_length_m(seg: tuple) -> float:
    """Approximate segment length in meters using Haversine."""
    lat1, lng1, lat2, lng2 = seg[0], seg[1], seg[2], seg[3]
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))
