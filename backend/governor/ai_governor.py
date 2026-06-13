"""
NEXUS AI Governor — autonomous city management agent.
Perception → Reasoning → Action loop powered by Gemini 1.5 Pro via LangChain.

Decision space is bounded: the governor can ONLY issue actions from a fixed
registry. It cannot do anything the system wasn't designed to support.

Confidence scoring:
  >= 0.85 → auto-execute
  0.60–0.84 → flag for human approval
  < 0.60 → hold, request human decision

Critical infrastructure decisions ALWAYS require human confirmation regardless of confidence.
"""
import asyncio
import json
import time
import os
from typing import Optional, Callable, Awaitable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from ..core.city_state import CityState, ZoneStatus

CRITICAL_ACTIONS = {"activate_backup_power", "begin_evacuation", "shed_substation_load"}
AUTO_EXECUTE_THRESHOLD = 0.85
HUMAN_APPROVAL_THRESHOLD = 0.60

SYSTEM_PROMPT = """You are NEXUS, the autonomous AI governor of Hyderabad.
You perceive the real-time state of the city and issue precise directives to protect lives.

Your decision space (you may ONLY issue these action types):
1. reroute_traffic(zone_id, divert_to: [zone_id, ...])
2. dispatch_resource(resource_id, destination_id)
3. activate_backup_power(hospital_id)  [CRITICAL — requires human confirmation]
4. shed_substation_load(substation_id, target_fraction: 0.0–1.0)  [CRITICAL]
5. begin_evacuation(zone_id)  [CRITICAL — requires human confirmation]
6. reposition_resources_preemptive(moves: [{resource_id, destination_id}, ...])
7. no_action(reason)

For each tick, respond with a JSON object:
{
  "reasoning": "2-3 sentences: what you see, what you predict, why you're acting",
  "action_type": "<one of the action types above>",
  "parameters": { ... action-specific parameters ... },
  "confidence": 0.0–1.0,
  "priority": "low|medium|high|critical",
  "reversible": true|false,
  "human_readable": "Plain English for the operator screen — what, why, how to reverse"
}

Principles:
- Every minute of emergency response delay costs lives. Act fast on high-confidence signals.
- Cascade failures propagate faster than humans can react. Preempt them.
- When uncertain, flag for human review rather than act on a low-confidence heuristic.
- Critical infrastructure (hospitals, substations) requires human confirmation.
- Explain every decision in plain English on the operator screen.
"""


class AIGovernor:
    def __init__(self, simulation_engine):
        self.engine = simulation_engine
        self._llm: Optional[ChatGoogleGenerativeAI] = None
        self._pending_approvals: list[dict] = []
        self._on_decision: Optional[Callable[[dict], Awaitable[None]]] = None
        self._governor_interval = 4.0  # seconds between perception-action cycles
        self._running = False
        self._decision_count = 0

    def on_decision(self, callback: Callable[[dict], Awaitable[None]]):
        self._on_decision = callback

    def _get_llm(self) -> ChatGoogleGenerativeAI:
        if self._llm is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY not set")
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=api_key,
                temperature=0.2,
                max_output_tokens=512,
            )
        return self._llm

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self._govern()
            except Exception as e:
                print(f"[Governor] Error: {e}")
            await asyncio.sleep(self._governor_interval)

    def stop(self):
        self._running = False

    async def approve_decision(self, decision_id: str, approved: bool):
        """Human operator approves or rejects a pending critical decision."""
        pending = next((d for d in self._pending_approvals if d["id"] == decision_id), None)
        if not pending:
            return
        self._pending_approvals = [d for d in self._pending_approvals if d["id"] != decision_id]

        if approved:
            result = self._execute_action(pending)
            pending["status"] = "approved_executed"
            pending["result"] = result
        else:
            pending["status"] = "rejected"

        if self._on_decision:
            await self._on_decision(pending)

    async def _govern(self):
        state = self.engine.state
        if state.scenario_active == "normal" and state.tick % 5 != 0:
            # Normal day: only act every 5 ticks (less frequent)
            return

        perception = self._build_perception(state)
        decision = await self._reason(perception)

        if not decision:
            return

        decision["id"] = f"dec_{self._decision_count:04d}"
        decision["tick"] = state.tick
        decision["timestamp"] = time.time()
        self._decision_count += 1

        action_type = decision.get("action_type", "no_action")
        confidence = decision.get("confidence", 0.0)
        is_critical = action_type in CRITICAL_ACTIONS

        if action_type == "no_action":
            decision["status"] = "no_action"
        elif is_critical or confidence < HUMAN_APPROVAL_THRESHOLD:
            decision["status"] = "pending_approval"
            self._pending_approvals.append(decision)
        elif confidence >= AUTO_EXECUTE_THRESHOLD:
            result = self._execute_action(decision)
            decision["status"] = "auto_executed"
            decision["result"] = result
        else:
            decision["status"] = "pending_approval"
            self._pending_approvals.append(decision)

        # Append to state's decision log
        state.recent_decisions.append({
            "id": decision["id"],
            "tick": decision["tick"],
            "action_type": action_type,
            "confidence": confidence,
            "priority": decision.get("priority", "medium"),
            "status": decision["status"],
            "human_readable": decision.get("human_readable", ""),
            "reasoning": decision.get("reasoning", ""),
            "reversible": decision.get("reversible", True),
            "parameters": decision.get("parameters", {}),
        })
        state.recent_decisions = state.recent_decisions[-50:]

        if self._on_decision:
            await self._on_decision(decision)

    def _build_perception(self, state: CityState) -> str:
        """Compress city state into a structured perception string for the LLM."""
        lines = [f"TICK {state.tick} | Scenario: {state.scenario_active}"]

        # Critical alerts first
        critical_zones = [z for z in state.zones.values() if z.status in (ZoneStatus.CRITICAL, ZoneStatus.WARNING)]
        if critical_zones:
            lines.append("\nCRITICAL ZONES:")
            for z in critical_zones:
                lines.append(f"  {z.name}: rainfall={z.rainfall_mm_per_hour:.0f}mm/hr, "
                             f"water={z.water_level_m:.2f}m, flooded={z.is_flooded}, status={z.status.value}")

        # Power system
        overloaded_subs = [s for s in state.substations.values() if s.overloaded]
        if overloaded_subs:
            lines.append("\nOVERLOADED SUBSTATIONS:")
            for s in overloaded_subs:
                lines.append(f"  {s.name}: {s.load_mw:.0f}/{s.max_load_mw:.0f}MW, flood_risk={s.flood_risk}")

        # Hospital power status
        at_risk_hosps = [h for h in state.hospitals.values() if not h.has_power or not h.accessible]
        if at_risk_hosps:
            lines.append("\nHOSPITAL ALERTS:")
            for h in at_risk_hosps:
                lines.append(f"  {h.name}: power={'YES' if h.has_power else 'NO'}, "
                             f"backup={'active' if h.backup_power_active else 'off'}, "
                             f"accessible={h.accessible}")

        # Available resources
        available = [r for r in state.resources.values() if r.status == "available"]
        lines.append(f"\nAVAILABLE RESOURCES: {len(available)} units")
        for r in available[:6]:  # limit to avoid token bloat
            lines.append(f"  {r.id} ({r.type.value}) at ({r.lat:.4f}, {r.lng:.4f})")

        # Cascade chain
        if state.cascade_chain:
            recent_cascade = state.cascade_chain[-3:]
            lines.append("\nCASCADE EVENTS (last 3):")
            for ev in recent_cascade:
                lines.append(f"  [{ev['severity'].upper()}] {ev['type']}: {ev.get('hospital', ev.get('substation', ''))}")

        # Last decision (avoid repeating it)
        if state.recent_decisions:
            last = state.recent_decisions[-1]
            lines.append(f"\nLAST ACTION: {last['action_type']} (tick {last['tick']}, status={last['status']})")

        return "\n".join(lines)

    async def _reason(self, perception: str) -> Optional[dict]:
        """Call Gemini with the perception and parse the JSON response."""
        try:
            llm = self._get_llm()
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=f"Current city state:\n{perception}\n\nIssue your directive:")
            ]
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: llm.invoke(messages)
            )
            text = response.content.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[Governor] JSON parse error: {e}")
            return None
        except Exception as e:
            print(f"[Governor] LLM error: {e}")
            return None

    def _execute_action(self, decision: dict) -> dict:
        """Execute the action against the simulation engine."""
        action_type = decision.get("action_type")
        params = decision.get("parameters", {})
        engine = self.engine

        dispatch = {
            "reroute_traffic": lambda: engine.reroute_traffic(
                params.get("zone_id"), params.get("divert_to", [])),
            "dispatch_resource": lambda: engine.dispatch_resource(
                params.get("resource_id"), params.get("destination_id")),
            "activate_backup_power": lambda: engine.activate_backup_power(
                params.get("hospital_id")),
            "shed_substation_load": lambda: engine.shed_substation_load(
                params.get("substation_id"), params.get("target_fraction", 0.7)),
            "begin_evacuation": lambda: engine.begin_evacuation(
                params.get("zone_id")),
            "reposition_resources_preemptive": lambda: engine.reposition_resources_preemptive(
                params.get("moves", [])),
            "no_action": lambda: {"action": "no_action"},
        }

        fn = dispatch.get(action_type)
        if fn:
            return fn()
        return {"error": f"Unknown action type: {action_type}"}
