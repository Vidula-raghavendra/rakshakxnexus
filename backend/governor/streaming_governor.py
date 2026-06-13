"""
NEXUS Streaming Governor — single model, visible reasoning.

One Gemini call per cycle. Output streams token-by-token to the frontend
so judges watch it think in real time:

  > PERCEIVE: Mehdipatnam 480mm/hr, flooded. Osmania: backup active.
  > RISK: AMB-01 route enters flood zone. Will strand in ~40s.
  > CONFLICT: Evacuation clears Mehdipatnam but pushes into Tolichowki.
  > DECISION: reroute_traffic | {"zone_id":"mehdipatnam_up","divert_to":["narayanguda_up"]} | confidence: 0.84 | priority: high | reversible: yes
  > REQUIRES_APPROVAL: no
  > HUMAN_READABLE: Closing Mehdipatnam entry, routing via NH65. Re-open by setting scenario to normal.

Parse the DECISION line to extract the action.
"""
import asyncio
import json
import os
import re
import time
import uuid
from typing import Optional, Callable, Awaitable

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from ..core.city_state import CityState, ZoneStatus
from ..core.city_graph import FLOOD_ZONES, HOSPITALS, SUBSTATIONS
from ..alerts.twilio_alerts import dispatch_action_alerts

CRITICAL_ACTIONS = {"activate_backup_power", "begin_evacuation", "shed_substation_load"}

SYSTEM_PROMPT = """You are NEXUS, the autonomous crisis governance AI for Hyderabad's City Operations Centre.
Your job: protect lives and critical infrastructure using real-time sensor data, CCTV detections, and city state.

Reason through the current city state in exactly these steps, one per line:

> PERCEIVE: Key facts right now — critical zones, CCTV incidents, infrastructure at risk, available resources. Be specific: camera IDs, incident types, confidence scores, named locations.

> RISK: The single most dangerous outcome in the next 2 minutes if you do nothing. Name the exact location and life-safety consequence.

> CONFLICT: Any competing priority that makes the obvious action dangerous. If none, say "None."

> DECISION: action_type | {compact JSON parameters} | confidence: 0.XX | priority: low/medium/high/critical | reversible: yes/no

> REQUIRES_APPROVAL: yes (for activate_backup_power, begin_evacuation, shed_substation_load, confidence < 0.65) | no

> HUMAN_READABLE: One sentence. What you're doing, why, and how an operator reverses it.

When CCTV detections show a road_accident: consider dispatch_resource (police/ambulance) and reroute_traffic away from that junction.

Action types:
- reroute_traffic          | {"zone_id":"...", "divert_to":["zone_id", ...]}
- dispatch_resource        | {"resource_id":"...", "destination_id":"..."}
- activate_backup_power    | {"hospital_id":"..."}
- shed_substation_load     | {"substation_id":"...", "target_fraction": 0.0-1.0}
- begin_evacuation         | {"zone_id":"..."}
- reposition_resources_preemptive | {"moves":[{"resource_id":"...","destination_id":"..."}]}
- no_action                | {}

Zone IDs: mehdipatnam_up, tolichowki_up, narayanguda_up, malakpet_up, lb_nagar_up
Hospital IDs: osmania_hospital, nims, apollo, yashoda, care_hospital
Substation IDs: sub_mehdipatnam, sub_banjara, sub_ameerpet, sub_lb_nagar
Resource IDs: amb_01, amb_02, amb_03, amb_04, fire_01, fire_02, pow_01, pow_02, pol_01, pol_02

Rules:
- Be specific. "Mehdipatnam Underpass" beats "Zone A". "AMB-01" beats "an ambulance".
- When uncertain, use no_action and say why.
- Never guess a resource or zone ID that isn't in the lists above.
"""


def _build_perception(state: CityState) -> str:
    lines = [f"TICK {state.tick} | Scenario: {state.scenario_active}"]

    critical = [z for z in state.zones.values() if z.status.value in ("critical", "warning")]
    if critical:
        lines.append("\nFLOOD ALERTS:")
        for z in critical:
            fz = next((f for f in FLOOD_ZONES if f["id"] == z.id), None)
            thresh = fz["flood_threshold_mm"] if fz else 300
            pct = int(z.rainfall_mm_per_hour / thresh * 100)
            lines.append(
                f"  {z.name}: {z.rainfall_mm_per_hour:.0f}mm/hr ({pct}% of threshold), "
                f"water={z.water_level_m:.2f}m, flooded={z.is_flooded}, status={z.status.value}"
            )

    overloaded = [s for s in state.substations.values() if s.overloaded]
    flood_risk_subs = [s for s in state.substations.values() if s.flood_risk]
    if overloaded or flood_risk_subs:
        lines.append("\nPOWER GRID:")
        for s in overloaded:
            lines.append(f"  OVERLOADED: {s.name} {s.load_mw:.0f}/{s.max_load_mw:.0f}MW")
        for s in flood_risk_subs:
            nearby_flooded = any(
                z.is_flooded for z in state.zones.values()
                if abs(z.lat - s.lat) < 0.025 and abs(z.lng - s.lng) < 0.025
            )
            if nearby_flooded:
                lines.append(f"  FLOOD RISK: {s.name} — adjacent zone is flooded")

    at_risk = [h for h in state.hospitals.values() if not h.has_power or h.backup_power_active]
    if at_risk:
        lines.append("\nHOSPITAL STATUS:")
        for h in at_risk:
            lines.append(
                f"  {h.name}: power={'YES' if h.has_power else 'NO'}, "
                f"backup={'ACTIVE' if h.backup_power_active else 'off'}, "
                f"accessible={h.accessible}"
            )

    available = [r for r in state.resources.values() if r.status == "available"]
    en_route  = [r for r in state.resources.values() if r.status == "en_route"]
    lines.append(f"\nRESOURCES: {len(available)} available, {len(en_route)} en route")
    for r in available[:5]:
        lines.append(f"  {r.id} ({r.type.value})")

    stranded = sum(1 for v in state.vehicles if v.get("status") == "stranded")
    if stranded:
        lines.append(f"\nVEHICLES: {stranded} stranded on road")

    # CCTV incidents from Rakshak (real detections, last 5 min)
    if hasattr(state, 'recent_rakshak_incidents') and state.recent_rakshak_incidents:
        lines.append("\nCCTV DETECTIONS (Rakshak / YOLOv8):")
        for inc in state.recent_rakshak_incidents[-5:]:
            age = int(time.time() - inc['timestamp'])
            lines.append(
                f"  [{inc['severity'].upper()}] {inc['incident_type']} — "
                f"{inc['camera_id']} | zone: {inc['zone_id'] or 'unknown'} | "
                f"conf={inc['confidence']:.0%} | {age}s ago | {inc['description']}"
            )

    if state.cascade_chain:
        lines.append("\nCASCADE (last 3):")
        for ev in state.cascade_chain[-3:]:
            lines.append(f"  [{ev['severity'].upper()}] {ev['type']}: {ev.get('hospital', ev.get('substation', ev.get('zone', '')))}")

    return "\n".join(lines)


def _rules_fallback(state: CityState) -> str:
    """Rules-based decision when Gemini is unavailable. Mimics the streamed format."""
    incidents = getattr(state, 'recent_rakshak_incidents', [])
    critical_zones = [z for z in state.zones.values() if z.status.value in ('critical', 'evacuating')]
    warning_zones  = [z for z in state.zones.values() if z.status.value == 'warning']

    # Pick the most severe recent incident
    inc = None
    for candidate in sorted(incidents, key=lambda i: i.get('timestamp', 0), reverse=True):
        if candidate.get('severity') in ('critical', 'high'):
            inc = candidate
            break

    # Determine action from incident type or zone state
    if inc:
        itype = inc.get('incident_type', '')
        zone  = inc.get('zone_id', 'unknown')
        conf  = inc.get('confidence', 0.80)
        cam   = inc.get('camera_id', 'unknown')
        desc  = inc.get('description', '')

        if itype in ('road_flood', 'vehicle_stranded'):
            action     = 'reroute_traffic'
            params     = json.dumps({"zone_id": zone, "divert_to": ["narayanguda_up", "lb_nagar_up"]})
            priority   = 'critical'
            reversible = 'yes'
            readable   = f"Rerouting traffic away from {zone} due to CCTV-confirmed flooding. Reverse by clearing zone status."
        elif itype in ('road_accident', 'road_blocked'):
            action     = 'dispatch_resource'
            params     = json.dumps({"resource_id": "AMB-01", "destination_id": zone})
            priority   = 'high'
            reversible = 'yes'
            readable   = f"Dispatching ambulance to {zone} — {cam} detected collision. Reverse by recalling resource."
        elif itype == 'crowd_surge':
            action     = 'dispatch_resource'
            params     = json.dumps({"resource_id": "POL-01", "destination_id": zone})
            priority   = 'high'
            reversible = 'yes'
            readable   = f"Dispatching police to {zone} — crowd surge detected by {cam}."
        else:
            action     = 'reroute_traffic'
            params     = json.dumps({"zone_id": zone, "divert_to": ["narayanguda_up"]})
            priority   = 'medium'
            reversible = 'yes'
            readable   = f"Precautionary reroute around {zone} based on CCTV incident ({itype})."

        perceive = f"CCTV cam {cam} reports {itype} ({int(conf*100)}% conf) at {zone}. {desc[:80]}"
        risk     = f"If unaddressed: secondary collisions or stranded persons at {zone} within 2 min."
        conflict = "None."
        approval = "no"

    elif critical_zones:
        z          = critical_zones[0]
        action     = 'begin_evacuation'
        params     = json.dumps({"zone_id": z.id})
        priority   = 'critical'
        reversible = 'no'
        readable   = f"Initiating evacuation of {z.name} — water level critical. Reverse by setting zone to normal."
        perceive   = f"{z.name} is CRITICAL — rainfall {z.rainfall_mm_per_hour:.0f}mm/hr, water {z.water_level_m:.2f}m."
        risk       = f"Trapped residents in {z.name} if evacuation delayed beyond next 2 minutes."
        conflict   = "Evacuation route may push crowd toward warning zones."
        approval   = "yes"
        conf       = 0.91

    elif warning_zones:
        z          = warning_zones[0]
        action     = 'reroute_traffic'
        params     = json.dumps({"zone_id": z.id, "divert_to": ["narayanguda_up"]})
        priority   = 'medium'
        reversible = 'yes'
        readable   = f"Precautionary reroute around {z.name} — water rising. Reverse when rainfall drops."
        perceive   = f"{z.name} at WARNING — rainfall {z.rainfall_mm_per_hour:.0f}mm/hr."
        risk       = f"Underpass flooding at {z.name} if rainfall continues at current rate."
        conflict   = "None."
        approval   = "no"
        conf       = 0.75

    else:
        action     = 'no_action'
        params     = '{}'
        priority   = 'low'
        reversible = 'yes'
        readable   = "City state nominal. No intervention required."
        perceive   = "All zones normal. No active CCTV incidents."
        risk       = "None identified."
        conflict   = "None."
        approval   = "no"
        conf       = 0.99

    return (
        f"> PERCEIVE: {perceive}\n"
        f"> RISK: {risk}\n"
        f"> CONFLICT: {conflict}\n"
        f"> DECISION: {action} | {params} | confidence: {conf:.2f} | priority: {priority} | reversible: {reversible}\n"
        f"> REQUIRES_APPROVAL: {approval}\n"
        f"> HUMAN_READABLE: {readable}\n"
        f"[Rules engine — Gemini quota exhausted]"
    )


def _parse_decision(full_text: str) -> dict:
    """Extract structured decision from the streamed reasoning text."""
    decision_line = re.search(r'> DECISION:\s*(.+)', full_text)
    approval_line = re.search(r'> REQUIRES_APPROVAL:\s*(\w+)', full_text)
    readable_line = re.search(r'> HUMAN_READABLE:\s*(.+)', full_text)

    action_type = "no_action"
    parameters  = {}
    confidence  = 0.5
    priority    = "medium"
    reversible  = True

    if decision_line:
        parts = [p.strip() for p in decision_line.group(1).split("|")]
        if parts:
            action_type = parts[0].strip()
        if len(parts) > 1:
            try:
                parameters = json.loads(parts[1])
            except json.JSONDecodeError:
                parameters = {}
        if len(parts) > 2:
            m = re.search(r'confidence:\s*([\d.]+)', parts[2])
            if m:
                confidence = float(m.group(1))
        if len(parts) > 3:
            m = re.search(r'priority:\s*(\w+)', parts[3])
            if m:
                priority = m.group(1)
        if len(parts) > 4:
            reversible = "yes" in parts[4].lower()

    requires_approval = False
    if approval_line:
        requires_approval = approval_line.group(1).lower() == "yes"
    requires_approval = requires_approval or (action_type in CRITICAL_ACTIONS) or (confidence < 0.65)

    human_readable = ""
    if readable_line:
        human_readable = readable_line.group(1).strip()

    # Extract PERCEIVE / RISK / CONFLICT as sub-fields for the decision feed
    perceive = ""
    risk     = ""
    conflict = ""
    pm = re.search(r'> PERCEIVE:\s*(.+?)(?=\n>|\Z)', full_text, re.DOTALL)
    rm = re.search(r'> RISK:\s*(.+?)(?=\n>|\Z)', full_text, re.DOTALL)
    cm = re.search(r'> CONFLICT:\s*(.+?)(?=\n>|\Z)', full_text, re.DOTALL)
    if pm: perceive = pm.group(1).strip()
    if rm: risk     = rm.group(1).strip()
    if cm: conflict = cm.group(1).strip()

    return {
        "action_type":         action_type,
        "parameters":          parameters,
        "confidence":          confidence,
        "priority":            priority,
        "reversible":          reversible,
        "requires_approval":   requires_approval,
        "human_readable":      human_readable,
        "reasoning":           full_text.strip(),
        "perceive":            perceive,
        "risk":                risk,
        "conflict":            conflict,
    }


class StreamingGovernor:
    def __init__(self, simulation_engine):
        self.engine = simulation_engine
        self._llm: Optional[ChatGoogleGenerativeAI] = None
        self._running = False
        self._govern_interval = 4.0
        self._decision_count = 0
        self._pending_approvals: list[dict] = []
        self._on_decision: Optional[Callable[[dict], Awaitable[None]]] = None
        self._on_stream_chunk: Optional[Callable[[dict], Awaitable[None]]] = None

    def on_decision(self, callback: Callable[[dict], Awaitable[None]]):
        self._on_decision = callback

    def on_stream_chunk(self, callback: Callable[[dict], Awaitable[None]]):
        """Called for each streamed token chunk — feeds the live terminal."""
        self._on_stream_chunk = callback

    def _get_llm(self) -> ChatGoogleGenerativeAI:
        if self._llm is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY not set")
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=api_key,
                temperature=0.2,
                max_output_tokens=600,
            )
        return self._llm

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self._govern()
            except Exception as e:
                print(f"[Governor] Error: {e}")
            await asyncio.sleep(self._govern_interval)

    def stop(self):
        self._running = False

    async def approve_decision(self, decision_id: str, approved: bool):
        pending = next((d for d in self._pending_approvals if d["id"] == decision_id), None)
        if not pending:
            return
        self._pending_approvals = [d for d in self._pending_approvals if d["id"] != decision_id]
        if approved:
            result = self._execute_action(pending["action_type"], pending.get("parameters", {}))
            pending["status"] = "approved_executed"
            pending["result"] = result
            asyncio.create_task(dispatch_action_alerts(pending))
        else:
            pending["status"] = "rejected"
        if self._on_decision:
            await self._on_decision(pending)

    async def _govern(self):
        state = self.engine.state

        # Govern when there's something worth governing
        has_alert = any(z.status.value in ("warning", "critical") for z in state.zones.values())
        is_flood  = state.scenario_active == "flood_sept2024"
        has_rakshak = bool(getattr(state, 'recent_rakshak_incidents', []))
        if not has_alert and not is_flood and not has_rakshak and state.tick % 10 != 0:
            return

        perception = _build_perception(state)
        session_id = str(uuid.uuid4())[:8]

        # Stream the reasoning
        full_text = ""
        try:
            llm = self._get_llm()
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=f"Current city state:\n{perception}\n\nReason through this:")
            ]

            # Signal thinking start
            if self._on_stream_chunk:
                await self._on_stream_chunk({
                    "session_id": session_id,
                    "chunk": "",
                    "thinking": True,
                })

            async for chunk in llm.astream(messages):
                token = chunk.content
                if token:
                    full_text += token
                    if self._on_stream_chunk:
                        await self._on_stream_chunk({
                            "session_id": session_id,
                            "chunk": token,
                            "thinking": False,
                        })

        except Exception as e:
            err_str = str(e)
            print(f"[Governor] Gemini unavailable, falling back to rules engine: {err_str[:120]}")
            full_text = _rules_fallback(state)
            if self._on_stream_chunk:
                await self._on_stream_chunk({
                    "session_id": session_id,
                    "chunk": full_text,
                    "thinking": False,
                })

        if not full_text:
            return

        # Parse the decision from the completed stream
        parsed = _parse_decision(full_text)

        self._decision_count += 1
        action_type = parsed["action_type"]
        confidence  = parsed["confidence"]

        if action_type == "no_action":
            status = "no_action"
        elif parsed["requires_approval"]:
            status = "pending_approval"
        elif confidence >= 0.85:
            status = "auto_executed"
        else:
            status = "pending_approval"

        decision = {
            "id":            f"dec_{self._decision_count:04d}",
            "tick":          state.tick,
            "session_id":    session_id,
            "action_type":   action_type,
            "parameters":    parsed["parameters"],
            "confidence":    confidence,
            "priority":      parsed["priority"],
            "reversible":    parsed["reversible"],
            "status":        status,
            "human_readable": parsed["human_readable"],
            "reasoning":     parsed["reasoning"],
            "perceive":      parsed["perceive"],
            "risk":          parsed["risk"],
            "conflict":      parsed["conflict"],
            "timestamp":     time.time(),
        }

        if status == "auto_executed":
            result = self._execute_action(action_type, parsed["parameters"])
            decision["result"] = result
            # Attach the triggering incident so the call message can name it
            recent = getattr(state, 'recent_rakshak_incidents', [])
            if recent:
                decision["trigger_incident"] = recent[-1]
            asyncio.create_task(dispatch_action_alerts(decision))
        elif status == "pending_approval":
            self._pending_approvals.append(decision)

        state.recent_decisions.append(decision)
        state.recent_decisions = state.recent_decisions[-50:]

        if self._on_decision:
            await self._on_decision(decision)

        # Signal stream complete
        if self._on_stream_chunk:
            await self._on_stream_chunk({
                "session_id": session_id,
                "chunk": "",
                "done": True,
                "decision": decision,
            })

    def _execute_action(self, action_type: str, params: dict) -> dict:
        engine = self.engine
        dispatch = {
            "reroute_traffic":                lambda: engine.reroute_traffic(
                params.get("zone_id"), params.get("divert_to", [])),
            "dispatch_resource":              lambda: engine.dispatch_resource(
                params.get("resource_id"), params.get("destination_id")),
            "activate_backup_power":          lambda: engine.activate_backup_power(
                params.get("hospital_id")),
            "shed_substation_load":           lambda: engine.shed_substation_load(
                params.get("substation_id"), params.get("target_fraction", 0.7)),
            "begin_evacuation":               lambda: engine.begin_evacuation(
                params.get("zone_id")),
            "reposition_resources_preemptive":lambda: engine.reposition_resources_preemptive(
                params.get("moves", [])),
            "no_action":                      lambda: {},
        }
        fn = dispatch.get(action_type)
        return fn() if fn else {"error": f"Unknown action: {action_type}"}
