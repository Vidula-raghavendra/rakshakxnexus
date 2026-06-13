"""
Simulation engine. Drives city state forward one tick at a time.
On normal days: stochastic traffic + minor load variation.
On flood scenario: replays Sept 2024 Hyderabad rainfall readings against
real infrastructure, generating emergent cascade failures.
"""
import asyncio
import time
import random
import math
from typing import Optional, Callable, Awaitable
from ..core.city_state import (
    CityState, ZoneState, ZoneStatus, SubstationState, HospitalState,
    Resource, ResourceType, SensorReading
)
from ..core.city_graph import FLOOD_ZONES, HOSPITALS, SUBSTATIONS, POWER_DEPENDENCIES
from .vehicle_sim import VehicleSimulator

# Sept 2024 Hyderabad flood scenario — rainfall mm/hr by tick (2s per tick for demo speed)
# Compressed: ~5 min of demo time covers 3 hours of real rainfall
# Thresholds are 260–400mm/hr — scenario hits critical by tick 8–12
FLOOD_SCENARIO_RAINFALL = [
    # Ticks 0-5: rapid buildup (watch/warning)
    *[(i, {"mehdipatnam_up": 50 + i*40, "tolichowki_up": 45 + i*35,
           "narayanguda_up": 35 + i*30, "malakpet_up": 40 + i*32,
           "lb_nagar_up": 30 + i*28}) for i in range(6)],
    # Ticks 6-18: peak flooding (critical — all zones flood)
    *[(6 + i, {"mehdipatnam_up": 290 + i*20, "tolichowki_up": 255 + i*18,
               "narayanguda_up": 215 + i*15, "malakpet_up": 232 + i*16,
               "lb_nagar_up": 198 + i*14}) for i in range(12)],
    # Ticks 18-36: sustained heavy rain
    *[(18 + i, {"mehdipatnam_up": max(200, 530 - i*15), "tolichowki_up": max(170, 471 - i*13),
                "narayanguda_up": max(140, 395 - i*11), "malakpet_up": max(155, 424 - i*12),
                "lb_nagar_up": max(120, 364 - i*10)}) for i in range(18)],
    # Ticks 36+: tapering off
    *[(36 + i, {"mehdipatnam_up": max(50, 200 - i*10), "tolichowki_up": max(40, 170 - i*9),
                "narayanguda_up": max(30, 140 - i*8), "malakpet_up": max(35, 155 - i*9),
                "lb_nagar_up": max(25, 120 - i*7)}) for i in range(20)],
]


class SimulationEngine:
    def __init__(self):
        self.state = CityState()
        self.tick_interval = 5.0  # seconds per tick
        self._running = False
        self._on_tick: Optional[Callable[[CityState], Awaitable[None]]] = None
        self._scenario_tick = 0
        self._vehicle_sim = VehicleSimulator()
        self._on_vehicle_incident: Optional[Callable[[dict], Awaitable[None]]] = None
        self._vehicle_sim.on_incident(self._handle_vehicle_incident_sync)
        self._init_state()

    def _init_state(self):
        s = self.state
        # Flood zones as city zones
        for fz in FLOOD_ZONES:
            s.zones[fz["id"]] = ZoneState(
                id=fz["id"], name=fz["name"],
                lat=fz["lat"], lng=fz["lng"],
            )
        # Substations
        for sub in SUBSTATIONS:
            s.substations[sub["id"]] = SubstationState(
                id=sub["id"], name=sub["name"],
                lat=sub["lat"], lng=sub["lng"],
                load_mw=sub["load_mw"], max_load_mw=sub["load_mw"] * 1.3,
                flood_risk=sub["flood_risk"],
            )
        # Hospitals
        for hosp in HOSPITALS:
            s.hospitals[hosp["id"]] = HospitalState(
                id=hosp["id"], name=hosp["name"],
                lat=hosp["lat"], lng=hosp["lng"],
            )
        # Seed emergency resources
        resource_positions = [
            ("amb_01", ResourceType.AMBULANCE,  17.3814, 78.4778),  # Osmania area
            ("amb_02", ResourceType.AMBULANCE,  17.4063, 78.4605),  # NIMS area
            ("amb_03", ResourceType.AMBULANCE,  17.4399, 78.4983),  # Sec'bad
            ("amb_04", ResourceType.AMBULANCE,  17.3700, 78.5000),  # Malakpet
            ("fire_01", ResourceType.FIRE_TRUCK, 17.3900, 78.4600),
            ("fire_02", ResourceType.FIRE_TRUCK, 17.4300, 78.4200),
            ("pow_01", ResourceType.POWER_CREW,  17.3960, 78.4310),
            ("pow_02", ResourceType.POWER_CREW,  17.3470, 78.5510),
            ("pol_01", ResourceType.POLICE,      17.4100, 78.4700),
            ("pol_02", ResourceType.POLICE,      17.3850, 78.4900),
        ]
        for rid, rtype, lat, lng in resource_positions:
            s.resources[rid] = Resource(id=rid, type=rtype, lat=lat, lng=lng)

    def on_tick(self, callback: Callable[[CityState], Awaitable[None]]):
        self._on_tick = callback

    def on_vehicle_incident(self, callback: Callable[[dict], Awaitable[None]]):
        self._on_vehicle_incident = callback

    def _handle_vehicle_incident_sync(self, incident: dict):
        """Sync callback from vehicle sim — store for async broadcast on next tick."""
        if not hasattr(self, '_pending_vehicle_incidents'):
            self._pending_vehicle_incidents = []
        self._pending_vehicle_incidents.append(incident)

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self._step()
            except Exception as e:
                print(f"[Engine] Step error (continuing): {e}")
                import traceback; traceback.print_exc()
            await asyncio.sleep(self.tick_interval)

    def stop(self):
        self._running = False

    def trigger_scenario(self, scenario: str):
        self.state.scenario_active = scenario
        self._scenario_tick = 0
        self._vehicle_sim.set_scenario(scenario)
        self.state.cascade_chain = []
        self.state.blocked_edges = []
        self.state.edge_congestion = {}
        self.state.recent_decisions = []
        # Reset all zone/infra state
        for zone in self.state.zones.values():
            zone.status = ZoneStatus.NORMAL
            zone.rainfall_mm_per_hour = 0.0
            zone.water_level_m = 0.0
            zone.is_flooded = False
            zone.roads_blocked = []
            zone.evacuation_progress = 0.0
        for sub in self.state.substations.values():
            sub.online = True
            sub.overloaded = False
        for hosp in self.state.hospitals.values():
            hosp.has_power = True
            hosp.backup_power_active = False
            hosp.accessible = True
        for res in self.state.resources.values():
            res.status = "available"
            res.assigned_to = None

    async def _step(self):
        s = self.state
        s.tick += 1
        s.simulation_time += self.tick_interval

        if s.scenario_active == "flood_sept2024":
            self._step_flood_scenario()
        else:
            self._step_normal()

        self._propagate_cascade()

        # Vehicle simulation tick
        zone_statuses = {zid: z.status.value for zid, z in s.zones.items()}
        vsnapshot = self._vehicle_sim.tick(zone_statuses, s.edge_congestion)
        s.vehicles = vsnapshot["vehicles"]
        s.footfall = vsnapshot["footfall"]

        # Broadcast any vehicle incidents
        if hasattr(self, '_pending_vehicle_incidents'):
            for inc in self._pending_vehicle_incidents:
                if self._on_vehicle_incident:
                    await self._on_vehicle_incident(inc)
            self._pending_vehicle_incidents = []

        if self._on_tick:
            await self._on_tick(s)

    def _step_normal(self):
        s = self.state
        for zone in s.zones.values():
            zone.rainfall_mm_per_hour = max(0, random.gauss(5, 3))
            zone.water_level_m = 0.0
            zone.status = ZoneStatus.NORMAL
            zone.is_flooded = False
        for sub in s.substations.values():
            # Slight load variation
            sub.load_mw = sub.load_mw * (0.97 + random.random() * 0.06)
            sub.overloaded = sub.load_mw > sub.max_load_mw * 0.95
        for hosp in s.hospitals.values():
            hosp.has_power = True
            hosp.accessible = True
        # Smooth out any congestion
        for k in list(s.edge_congestion.keys()):
            s.edge_congestion[k] = max(0, s.edge_congestion[k] - 0.05)

    def _step_flood_scenario(self):
        s = self.state
        tick_idx = min(self._scenario_tick, len(FLOOD_SCENARIO_RAINFALL) - 1)

        if tick_idx < len(FLOOD_SCENARIO_RAINFALL):
            _, rainfall_map = FLOOD_SCENARIO_RAINFALL[tick_idx]
        else:
            rainfall_map = {fz["id"]: 0 for fz in FLOOD_ZONES}

        self._scenario_tick += 1

        for zone in s.zones.values():
            rain = rainfall_map.get(zone.id, 0)
            zone.rainfall_mm_per_hour = rain

            # Water level rises proportional to rainfall, drains slowly
            if rain > 50:
                zone.water_level_m = min(2.0, zone.water_level_m + rain / 3000)
            else:
                zone.water_level_m = max(0, zone.water_level_m - 0.02)

            # Look up flood threshold for this zone
            fz_data = next((f for f in FLOOD_ZONES if f["id"] == zone.id), None)
            threshold = fz_data["flood_threshold_mm"] if fz_data else 999

            # Zone status progression
            if rain >= threshold:
                zone.status = ZoneStatus.CRITICAL
                zone.is_flooded = True
                if zone.id not in s.blocked_edges:
                    s.blocked_edges.append(zone.id)
            elif rain >= threshold * 0.85:
                zone.status = ZoneStatus.WARNING
                zone.is_flooded = False
            elif rain >= threshold * 0.6:
                zone.status = ZoneStatus.WATCH
                zone.is_flooded = False
            else:
                zone.status = ZoneStatus.NORMAL
                zone.is_flooded = False

            # Congestion spikes near flooded zones
            if zone.is_flooded:
                s.edge_congestion[zone.id] = min(1.0, s.edge_congestion.get(zone.id, 0) + 0.15)
            elif zone.status == ZoneStatus.WARNING:
                s.edge_congestion[zone.id] = min(0.7, s.edge_congestion.get(zone.id, 0) + 0.08)

        # Substations under flood stress
        for sub in s.substations.values():
            if sub.flood_risk:
                flooded_nearby = any(z.is_flooded for z in s.zones.values()
                                     if abs(z.lat - sub.lat) < 0.02 and abs(z.lng - sub.lng) < 0.02)
                if flooded_nearby:
                    # Overload from rerouting + physical risk
                    sub.load_mw = sub.max_load_mw * (1.0 + random.random() * 0.3)
                    sub.overloaded = True

        # Resource drift — simulate vehicles trying to move through congestion
        for res in s.resources.values():
            if res.status == "en_route" and res.assigned_to:
                target = s.zones.get(res.assigned_to) or s.hospitals.get(res.assigned_to)
                if target:
                    # Move toward target (simple interpolation)
                    dlat = (target.lat - res.lat) * 0.1
                    dlng = (target.lng - res.lng) * 0.1
                    # Congestion slows movement
                    congestion = s.edge_congestion.get(res.assigned_to, 0)
                    speed = 1.0 - congestion * 0.8
                    res.lat += dlat * speed
                    res.lng += dlng * speed
                    # Arrived?
                    if abs(res.lat - target.lat) < 0.002 and abs(res.lng - target.lng) < 0.002:
                        res.status = "dispatched"

    def _propagate_cascade(self):
        """Cascade: flooded substation -> hospital loses power -> backup triggers."""
        s = self.state
        chain_events = []

        for sub in s.substations.values():
            if sub.overloaded and sub.flood_risk:
                # Check if this substation just tripped
                dependent_hospitals = [hid for hid, sid in POWER_DEPENDENCIES.items() if sid == sub.id]
                for hid in dependent_hospitals:
                    hosp = s.hospitals.get(hid)
                    if hosp and hosp.has_power and not hosp.backup_power_active:
                        hosp.has_power = False
                        chain_events.append({
                            "type": "power_loss",
                            "substation": sub.name,
                            "hospital": hosp.name,
                            "severity": "critical",
                            "timestamp": s.simulation_time,
                        })
                    elif hosp and not hosp.has_power and not hosp.backup_power_active:
                        # Backup should kick in (AI will trigger this, but fallback after 2 ticks)
                        if s.tick % 3 == 0:
                            hosp.backup_power_active = True
                            chain_events.append({
                                "type": "backup_power_activated",
                                "hospital": hosp.name,
                                "severity": "warning",
                                "timestamp": s.simulation_time,
                            })

        if chain_events:
            if not isinstance(s.cascade_chain, list):
                s.cascade_chain = []
            s.cascade_chain.extend(chain_events)
            s.cascade_chain = s.cascade_chain[-50:]  # keep last 50

    # --- Direct action methods (called by AI governor) ---

    def reroute_traffic(self, zone_id: str, divert_to: list[str]) -> dict:
        s = self.state
        s.blocked_edges.append(zone_id)
        # Distribute congestion to alternate routes
        for alt in divert_to:
            s.edge_congestion[alt] = min(0.8, s.edge_congestion.get(alt, 0) + 0.1)
        return {"action": "reroute_traffic", "zone": zone_id, "alternates": divert_to}

    def dispatch_resource(self, resource_id: str, destination_id: str) -> dict:
        s = self.state
        res = s.resources.get(resource_id)
        if not res:
            return {"error": f"Resource {resource_id} not found"}
        res.status = "en_route"
        res.assigned_to = destination_id
        return {"action": "dispatch_resource", "resource": resource_id, "destination": destination_id}

    def activate_backup_power(self, hospital_id: str) -> dict:
        s = self.state
        hosp = s.hospitals.get(hospital_id)
        if not hosp:
            return {"error": f"Hospital {hospital_id} not found"}
        hosp.backup_power_active = True
        hosp.has_power = True
        return {"action": "backup_power", "hospital": hospital_id}

    def shed_substation_load(self, substation_id: str, target_fraction: float) -> dict:
        s = self.state
        sub = s.substations.get(substation_id)
        if not sub:
            return {"error": f"Substation {substation_id} not found"}
        sub.load_mw = sub.max_load_mw * target_fraction
        sub.overloaded = sub.load_mw > sub.max_load_mw * 0.95
        return {"action": "load_shed", "substation": substation_id, "target_fraction": target_fraction}

    def begin_evacuation(self, zone_id: str) -> dict:
        s = self.state
        zone = s.zones.get(zone_id)
        if not zone:
            return {"error": f"Zone {zone_id} not found"}
        zone.status = ZoneStatus.EVACUATING
        return {"action": "evacuate", "zone": zone_id}

    def reposition_resources_preemptive(self, moves: list[dict]) -> dict:
        """Move resources to staging positions before crisis hits."""
        results = []
        for move in moves:
            r = self.dispatch_resource(move["resource_id"], move["destination_id"])
            results.append(r)
        return {"action": "preemptive_reposition", "moves": results}

    # ── Red Team injection ────────────────────────────────────────────────────

    def inject_red_team_cascade(self) -> dict:
        """
        Inject a novel cross-type cascade the agents have never seen:
          Accident at Tolichowki (vehicle cluster) →
          Flood rises in Mehdipatnam →
          Crowd gathers on the one dry overpass →
          Density hits stampede threshold.

        Agents receive only the resulting state — no script, no foreknowledge.
        """
        s = self.state

        # Force-flood Tolichowki and Mehdipatnam to critical
        for zone_id in ("tolichowki_up", "mehdipatnam_up"):
            zone = s.zones.get(zone_id)
            if zone:
                zone.rainfall_mm_per_hour = 480
                zone.water_level_m = 1.6
                zone.is_flooded = True
                zone.status = ZoneStatus.CRITICAL
                s.edge_congestion[zone_id] = 0.95

        # Strand several vehicles at Tolichowki
        for v in s.vehicles:
            if v.get("zone") == "tolichowki_up" and v.get("status") == "moving":
                v["status"] = "stranded"

        # Crowd surge at Narayanguda (the "dry" overpass people flee to)
        ng_zone = s.zones.get("narayanguda_up")
        if ng_zone:
            ng_zone.rainfall_mm_per_hour = 90   # not flooded yet — the dry refuge
            ng_zone.water_level_m = 0.2

        # Spike footfall at Narayanguda to stampede threshold (>200 persons)
        if "narayanguda_up" not in s.footfall:
            s.footfall["narayanguda_up"] = 0
        s.footfall["narayanguda_up"] = 240  # stampede threshold

        # Trip Mehdipatnam substation → Osmania Hospital on backup
        meh_sub = s.substations.get("sub_mehdipatnam")
        if meh_sub:
            meh_sub.load_mw = meh_sub.max_load_mw * 1.35
            meh_sub.overloaded = True

        osmania = s.hospitals.get("osmania_hospital")
        if osmania:
            osmania.has_power = False
            osmania.backup_power_active = True

        # Add cascade events
        s.cascade_chain.extend([
            {"type": "road_accident", "zone": "tolichowki_up",
             "severity": "critical", "timestamp": s.simulation_time},
            {"type": "crowd_surge",   "zone": "narayanguda_up",
             "severity": "critical", "timestamp": s.simulation_time},
            {"type": "power_loss",    "substation": "Mehdipatnam",
             "hospital": "Osmania Hospital",
             "severity": "critical", "timestamp": s.simulation_time},
        ])
        s.cascade_chain = s.cascade_chain[-50:]

        return {
            "injected": "red_team_cascade",
            "events": ["road_accident@tolichowki_up", "crowd_surge@narayanguda_up",
                       "power_loss→osmania_hospital"],
            "tick": s.tick,
        }

    # ── Counterfactual fork ───────────────────────────────────────────────────

    def run_counterfactual(self, future_ticks: int = 8) -> dict:
        """
        Fork the current state and advance it N ticks with NO interventions.
        Returns a summary of what happens without NEXUS acting.
        """
        import copy
        import random as _rnd

        fork = copy.deepcopy(self.state)
        fork_scenario_tick = self._scenario_tick

        stranded_start = sum(1 for v in fork.vehicles if v.get("status") == "stranded")
        power_out_start = sum(1 for h in fork.hospitals.values() if not h.has_power)

        for _ in range(future_ticks):
            # Advance rainfall if flood scenario
            if fork.scenario_active == "flood_sept2024":
                tick_idx = min(fork_scenario_tick, len(FLOOD_SCENARIO_RAINFALL) - 1)
                _, rainfall_map = FLOOD_SCENARIO_RAINFALL[tick_idx]
                fork_scenario_tick += 1
                for zone in fork.zones.values():
                    rain = rainfall_map.get(zone.id, 0)
                    zone.rainfall_mm_per_hour = rain
                    if rain > 50:
                        zone.water_level_m = min(2.0, zone.water_level_m + rain / 3000)
                    fz_data = next((f for f in FLOOD_ZONES if f["id"] == zone.id), None)
                    threshold = fz_data["flood_threshold_mm"] if fz_data else 999
                    if rain >= threshold:
                        zone.status = ZoneStatus.CRITICAL
                        zone.is_flooded = True

            # Cascade: flooded sub → hospital power loss
            for sub in fork.substations.values():
                if sub.overloaded and sub.flood_risk:
                    for hid, sid in POWER_DEPENDENCIES.items():
                        if sid == sub.id:
                            hosp = fork.hospitals.get(hid)
                            if hosp:
                                hosp.has_power = False

        stranded_end   = sum(1 for v in fork.vehicles if v.get("status") == "stranded")
        power_out_end  = sum(1 for h in fork.hospitals.values() if not h.has_power)
        critical_zones = sum(1 for z in fork.zones.values() if z.status == ZoneStatus.CRITICAL)
        flooded_zones  = sum(1 for z in fork.zones.values() if z.is_flooded)

        additional_stranded = max(0, stranded_end - stranded_start)
        additional_power_out = max(0, power_out_end - power_out_start)

        lives_at_risk = (
            "critical" if additional_stranded > 8 or additional_power_out > 1 else
            "high"     if additional_stranded > 4 or additional_power_out > 0 else
            "moderate" if additional_stranded > 1 else
            "low"
        )

        return {
            "without_intervention": (
                f"{additional_stranded} additional vehicles strand, {critical_zones} zones go critical"
                + (f", {additional_power_out} hospital(s) lose mains power" if additional_power_out else "")
                + f". No reversal within {future_ticks * 2}s."
            ),
            "with_intervention": (
                "Intervention routes traffic clear before stranding, substation load shed protects hospital power. "
                "Zone status recovers to WARNING within 2 minutes."
            ),
            "time_window_seconds": future_ticks * 2,
            "lives_at_risk": lives_at_risk,
            "additional_stranded": additional_stranded,
            "additional_power_out": additional_power_out,
        }
