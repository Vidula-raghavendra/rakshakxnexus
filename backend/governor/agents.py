"""
NEXUS Multi-Agent System.

Four specialized agents run in parallel, each calling Gemini 1.5 Pro with
a distinct system prompt and objective. Their outputs go to a Governor Agent
that adjudicates conflicts and issues the final directive.

Agent roster:
  EmergencyAgent      — minimize mortality, fast dispatch, aggressive
  InfrastructureAgent — keep grid/hospitals online, conservative
  AdversaryAgent      — find failure modes others missed, stress-tester
  SimulationAgent     — runs counterfactual, quantifies intervention value

All agents produce structured JSON. Governor reads all four, surfaces conflicts,
picks the best action, and logs the reasoning chain for accountability.
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from ..core.city_state import CityState, ZoneStatus

# ── Agent output schema ───────────────────────────────────────────────────────

@dataclass
class AgentVote:
    agent_id: str          # "emergency" | "infrastructure" | "adversary" | "simulation"
    agent_name: str
    recommended_action: str
    parameters: dict
    confidence: float
    reasoning: str         # what it sees and why it's recommending this
    risk_flag: str         # what it's worried about
    priority: str          # low|medium|high|critical
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "recommended_action": self.recommended_action,
            "parameters": self.parameters,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "risk_flag": self.risk_flag,
            "priority": self.priority,
            "timestamp": self.timestamp,
        }


@dataclass
class GovernorDecision:
    id: str
    tick: int
    action_type: str
    parameters: dict
    confidence: float
    priority: str
    status: str            # auto_executed | pending_approval | no_action | rejected
    human_readable: str
    governor_reasoning: str   # how it adjudicated the agents
    agent_votes: list[dict]   # what each agent said
    conflicts: list[str]      # conflicts detected between agents
    reversible: bool
    timestamp: float = field(default_factory=time.time)
    counterfactual: Optional[dict] = None  # what happens if we DON'T act
    override_majority: bool = False        # Governor went against highest-conf vote
    adversary_veto_applied: bool = False   # Adversary flag changed the outcome

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tick": self.tick,
            "action_type": self.action_type,
            "parameters": self.parameters,
            "confidence": self.confidence,
            "priority": self.priority,
            "status": self.status,
            "human_readable": self.human_readable,
            "reasoning": self.governor_reasoning,
            "agent_votes": self.agent_votes,
            "conflicts": self.conflicts,
            "reversible": self.reversible,
            "timestamp": self.timestamp,
            "counterfactual": self.counterfactual,
            "override_majority": self.override_majority,
            "adversary_veto_applied": self.adversary_veto_applied,
        }


# ── System prompts ────────────────────────────────────────────────────────────

EMERGENCY_PROMPT = """You are the NEXUS Emergency Agent.
Your SOLE objective: minimize casualties and emergency response time.
You are aggressive. You dispatch resources fast. You accept infrastructure risk to save lives.
You do NOT worry about grid stability or long-term consequences — that is another agent's job.

Given the city state, respond with exactly this JSON:
{
  "recommended_action": "<action_type>",
  "parameters": { ...action params... },
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentences: what life-safety threat you see and why this action addresses it",
  "risk_flag": "what you're most worried about right now",
  "priority": "low|medium|high|critical"
}

Action types: reroute_traffic, dispatch_resource, activate_backup_power, shed_substation_load, begin_evacuation, reposition_resources_preemptive, no_action
Parameter structures:
  reroute_traffic: {"zone_id": "...", "divert_to": ["zone_id"]}
  dispatch_resource: {"resource_id": "...", "destination_id": "..."}
  activate_backup_power: {"hospital_id": "..."}
  shed_substation_load: {"substation_id": "...", "target_fraction": 0.7}
  begin_evacuation: {"zone_id": "..."}
  reposition_resources_preemptive: {"moves": [{"resource_id": "...", "destination_id": "..."}]}
  no_action: {}
"""

INFRASTRUCTURE_PROMPT = """You are the NEXUS Infrastructure Agent.
Your SOLE objective: keep critical infrastructure online — grid, hospitals, power supply.
You are conservative. You prefer preemptive load-shedding over cascade failure.
You do NOT prioritize emergency response speed — that is another agent's job.

Given the city state, respond with exactly this JSON:
{
  "recommended_action": "<action_type>",
  "parameters": { ...action params... },
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentences: what infrastructure risk you see and why this action protects it",
  "risk_flag": "what cascade failure you're predicting right now",
  "priority": "low|medium|high|critical"
}

Action types: reroute_traffic, dispatch_resource, activate_backup_power, shed_substation_load, begin_evacuation, reposition_resources_preemptive, no_action
Parameter structures same as above.
Substations: sub_mehdipatnam (flood risk, serves Osmania Hospital), sub_banjara (serves NIMS, Apollo), sub_ameerpet (serves Yashoda), sub_lb_nagar (flood risk)
"""

ADVERSARY_PROMPT = """You are the NEXUS Adversary Agent.
Your job is to find the failure mode that the Emergency and Infrastructure agents have MISSED.
You assume both agents are about to recommend something that has an unintended consequence.
You are not proposing an action — you are flagging the WORST THING THAT COULD HAPPEN given current city state.

Respond with exactly this JSON:
{
  "recommended_action": "no_action",
  "parameters": {},
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentences: the hidden risk or second-order effect the other agents are missing",
  "risk_flag": "the specific cascade failure or conflict you are warning about — be specific with names",
  "priority": "low|medium|high|critical"
}

Think about:
- Routes that will lose power imminently
- Resources being sent somewhere that will become inaccessible
- Actions that solve one problem but create a worse one 60-90 seconds later
- Compounding failures when two systems fail simultaneously
"""

SIMULATION_PROMPT = """You are the NEXUS Simulation Agent.
Your job: quantify the cost of NOT acting and the value of the most likely intervention.
You run the counterfactual in your head.

Given city state, respond with exactly this JSON:
{
  "recommended_action": "<most_likely_best_action>",
  "parameters": { ...action params... },
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentences: what you predict happens in the next 5 minutes with vs without intervention",
  "risk_flag": "the specific outcome metric that degrades most if no action is taken",
  "priority": "low|medium|high|critical",
  "counterfactual": {
    "without_intervention": "specific prediction: what fails, how long, impact on hospitals/ambulances",
    "with_intervention": "specific prediction: what is preserved, at what cost",
    "time_window_seconds": 120,
    "lives_at_risk": "qualitative estimate: none|low|moderate|high|critical"
  }
}
"""

GOVERNOR_PROMPT = """You are the NEXUS Governor Agent.
You adjudicate four specialist agents and issue ONE binding directive.

CONFLICT-RESOLUTION POLICY (apply in this order):
1. ADVERSARY VETO: If the Adversary flags a dependency or cascade the other agents missed,
   that flag can BLOCK the majority recommendation. Check: does the Adversary's risk_flag describe
   a failure that would make Emergency's or Infrastructure's recommended action counterproductive?
   If yes, override both and choose a safer action.

2. IRREVERSIBILITY WEIGHT: When Emergency and Infrastructure conflict on a shared resource,
   weigh by irreversibility × lives-at-risk-delta from the Simulation Agent.
   Formula: if (irreversible action) AND (Simulation says lives_at_risk == "high" or "critical"),
   bias toward the action that preserves the most future options.

3. MAJORITY OVERRIDE: You MAY override the highest-confidence vote if:
   - Adversary veto applies (see rule 1), OR
   - The recommended action would deplete the LAST available resource of a type, OR
   - Simulation Agent forecasts a worse outcome in the 8-minute window.
   When overriding, you MUST name which agent you are overriding and why.

4. CONSENSUS: If agents agree (all same action_type), follow consensus. Reduce confidence by 0.1
   if Adversary's risk_flag is non-trivial.

OUTPUT — respond with exactly this JSON:
{
  "action_type": "<final_action>",
  "parameters": { ...action params... },
  "confidence": 0.0-1.0,
  "priority": "low|medium|high|critical",
  "reversible": true|false,
  "human_readable": "Plain English for the operator: what, why, how to reverse",
  "governor_reasoning": "2-3 sentences: name which agents you sided with or overrode and WHY. If overriding majority, say so explicitly: 'Overriding [Agent] because [reason].'",
  "conflicts": ["<each conflict between agents as a string>"],
  "override_majority": true|false,
  "adversary_veto_applied": true|false,
  "requires_human_approval": true|false
}

requires_human_approval = true for: activate_backup_power, shed_substation_load, begin_evacuation, confidence < 0.65.
override_majority = true if you went against the highest-confidence non-Adversary vote.
adversary_veto_applied = true if the Adversary's risk_flag directly changed your action.
"""


# ── Individual agents ─────────────────────────────────────────────────────────

class SpecialistAgent:
    def __init__(self, agent_id: str, name: str, system_prompt: str, color: str):
        self.agent_id = agent_id
        self.name = name
        self.system_prompt = system_prompt
        self.color = color
        self._llm: Optional[ChatGoogleGenerativeAI] = None
        self.last_vote: Optional[AgentVote] = None
        self.is_thinking = False

    def _get_llm(self) -> ChatGoogleGenerativeAI:
        if self._llm is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY not set")
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=api_key,
                temperature=0.3,
                max_output_tokens=400,
            )
        return self._llm

    async def vote(self, perception: str) -> Optional[AgentVote]:
        self.is_thinking = True
        try:
            llm = self._get_llm()
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=f"City state:\n{perception}\n\nYour assessment:")
            ]
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: llm.invoke(messages)
            )
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)

            vote = AgentVote(
                agent_id=self.agent_id,
                agent_name=self.name,
                recommended_action=data.get("recommended_action", "no_action"),
                parameters=data.get("parameters", {}),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                risk_flag=data.get("risk_flag", ""),
                priority=data.get("priority", "medium"),
            )
            # Simulation agent also has counterfactual
            if self.agent_id == "simulation" and "counterfactual" in data:
                vote.parameters["_counterfactual"] = data["counterfactual"]

            self.last_vote = vote
            return vote
        except Exception as e:
            print(f"[{self.name}] Error: {e}")
            return None
        finally:
            self.is_thinking = False


# ── Governor (orchestrator) ───────────────────────────────────────────────────

CRITICAL_ACTIONS = {"activate_backup_power", "begin_evacuation", "shed_substation_load"}

class MultiAgentGovernor:
    """
    Runs 4 specialist agents in parallel, feeds their votes to the Governor LLM,
    which adjudicates and issues a final directive.
    """

    def __init__(self, simulation_engine):
        self.engine = simulation_engine

        self.agents = {
            "emergency":      SpecialistAgent("emergency",      "Emergency Agent",      EMERGENCY_PROMPT,      "#ef4444"),
            "infrastructure": SpecialistAgent("infrastructure", "Infrastructure Agent", INFRASTRUCTURE_PROMPT, "#3b82f6"),
            "adversary":      SpecialistAgent("adversary",      "Adversary Agent",      ADVERSARY_PROMPT,      "#f97316"),
            "simulation":     SpecialistAgent("simulation",     "Simulation Agent",     SIMULATION_PROMPT,     "#a855f7"),
        }

        self._governor_llm: Optional[ChatGoogleGenerativeAI] = None
        self._pending_approvals: list[dict] = []
        self._on_decision: Optional[Callable[[dict], Awaitable[None]]] = None
        self._on_agent_update: Optional[Callable[[dict], Awaitable[None]]] = None
        self._governor_interval = 6.0  # 4 parallel LLM calls + governor = ~5s, so 6s cycle
        self._running = False
        self._decision_count = 0

    def on_decision(self, callback: Callable[[dict], Awaitable[None]]):
        self._on_decision = callback

    def on_agent_update(self, callback: Callable[[dict], Awaitable[None]]):
        """Called whenever an agent produces a vote (for live UI streaming)."""
        self._on_agent_update = callback

    def _get_governor_llm(self) -> ChatGoogleGenerativeAI:
        if self._governor_llm is None:
            api_key = os.getenv("GEMINI_API_KEY")
            self._governor_llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=api_key,
                temperature=0.1,  # governor is highly deterministic
                max_output_tokens=600,
            )
        return self._governor_llm

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self._govern()
            except Exception as e:
                print(f"[Governor] Cycle error: {e}")
                import traceback; traceback.print_exc()
            await asyncio.sleep(self._governor_interval)

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
        else:
            pending["status"] = "rejected"

        if self._on_decision:
            await self._on_decision(pending)

    async def _govern(self):
        state = self.engine.state

        # During normal ops, cycle less often
        if state.scenario_active == "normal" and state.tick % 5 != 0:
            return

        # Only govern if there's something worth governing
        has_alert = any(
            z.status.value in ("warning", "critical", "evacuating")
            for z in state.zones.values()
        )
        is_flood = state.scenario_active == "flood_sept2024"
        if not has_alert and not is_flood and state.tick % 10 != 0:
            return

        perception = self._build_perception(state)

        # ── Step 1: Run all 4 agents in parallel ─────────────────────────────
        vote_tasks = [
            agent.vote(perception)
            for agent in self.agents.values()
        ]
        votes_raw = await asyncio.gather(*vote_tasks, return_exceptions=True)
        votes: list[AgentVote] = [v for v in votes_raw if isinstance(v, AgentVote)]

        if not votes:
            return

        # Stream agent votes to UI immediately (before governor adjudicates)
        if self._on_agent_update:
            for vote in votes:
                await self._on_agent_update({
                    "type": "agent_vote",
                    "data": vote.to_dict(),
                })

        # ── Step 2: Governor adjudicates ──────────────────────────────────────
        decision_raw = await self._adjudicate(perception, votes)
        if not decision_raw:
            return

        # ── Step 3: Extract counterfactual from simulation agent ──────────────
        sim_vote = next((v for v in votes if v.agent_id == "simulation"), None)
        counterfactual = None
        if sim_vote:
            counterfactual = sim_vote.parameters.pop("_counterfactual", None)

        # ── Step 4: Build GovernorDecision ────────────────────────────────────
        self._decision_count += 1
        action_type = decision_raw.get("action_type", "no_action")
        confidence = float(decision_raw.get("confidence", 0.5))
        is_critical = action_type in CRITICAL_ACTIONS
        needs_human = decision_raw.get("requires_human_approval", False) or is_critical or confidence < 0.65

        if action_type == "no_action":
            status = "no_action"
        elif needs_human:
            status = "pending_approval"
        elif confidence >= 0.85:
            status = "auto_executed"
        else:
            status = "pending_approval"

        decision = GovernorDecision(
            id=f"dec_{self._decision_count:04d}",
            tick=state.tick,
            action_type=action_type,
            parameters=decision_raw.get("parameters", {}),
            confidence=confidence,
            priority=decision_raw.get("priority", "medium"),
            status=status,
            human_readable=decision_raw.get("human_readable", ""),
            governor_reasoning=decision_raw.get("governor_reasoning", ""),
            agent_votes=[v.to_dict() for v in votes],
            conflicts=decision_raw.get("conflicts", []),
            reversible=decision_raw.get("reversible", True),
            counterfactual=counterfactual,
            override_majority=bool(decision_raw.get("override_majority", False)),
            adversary_veto_applied=bool(decision_raw.get("adversary_veto_applied", False)),
        )

        # ── Step 5: Execute or queue ──────────────────────────────────────────
        d = decision.to_dict()
        if status == "auto_executed":
            result = self._execute_action(action_type, decision.parameters)
            d["result"] = result
        elif status == "pending_approval":
            self._pending_approvals.append(d)

        # Log to state
        state.recent_decisions.append(d)
        state.recent_decisions = state.recent_decisions[-50:]

        if self._on_decision:
            await self._on_decision(d)

    async def _adjudicate(self, perception: str, votes: list[AgentVote]) -> Optional[dict]:
        """Governor LLM reads all agent votes and issues final directive."""
        votes_text = "\n".join([
            f"[{v.agent_name}] recommends: {v.recommended_action}\n"
            f"  Reasoning: {v.reasoning}\n"
            f"  Risk flag: {v.risk_flag}\n"
            f"  Confidence: {v.confidence:.0%}\n"
            for v in votes
        ])

        try:
            llm = self._get_governor_llm()
            messages = [
                SystemMessage(content=GOVERNOR_PROMPT),
                HumanMessage(content=(
                    f"City state:\n{perception}\n\n"
                    f"Agent votes:\n{votes_text}\n\n"
                    f"Issue your final directive:"
                ))
            ]
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: llm.invoke(messages)
            )
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as e:
            print(f"[Governor] Adjudication error: {e}")
            return None

    def _build_perception(self, state: CityState) -> str:
        lines = [f"TICK {state.tick} | Scenario: {state.scenario_active} | Time: {state.simulation_time:.0f}s"]

        critical_zones = [z for z in state.zones.values() if z.status.value in ("critical", "warning")]
        if critical_zones:
            lines.append("\nFLOOD ALERTS:")
            for z in critical_zones:
                threshold = {"mehdipatnam_up": 400, "tolichowki_up": 350,
                             "narayanguda_up": 280, "malakpet_up": 310, "lb_nagar_up": 260}.get(z.id, 300)
                pct = int(z.rainfall_mm_per_hour / threshold * 100)
                lines.append(f"  {z.name}: {z.rainfall_mm_per_hour:.0f}mm/hr ({pct}% of flood threshold), "
                             f"water={z.water_level_m:.2f}m, flooded={z.is_flooded}, status={z.status.value}")

        overloaded = [s for s in state.substations.values() if s.overloaded]
        at_risk_subs = [s for s in state.substations.values() if s.flood_risk]
        if overloaded or at_risk_subs:
            lines.append("\nPOWER GRID:")
            for s in overloaded:
                lines.append(f"  OVERLOADED: {s.name} {s.load_mw:.0f}/{s.max_load_mw:.0f}MW")
            for s in at_risk_subs:
                nearby_flooded = any(z.is_flooded for z in state.zones.values()
                                     if abs(z.lat - s.lat) < 0.025 and abs(z.lng - s.lng) < 0.025)
                if nearby_flooded:
                    lines.append(f"  FLOOD RISK: {s.name} — adjacent zone is flooded")

        at_risk_h = [h for h in state.hospitals.values() if not h.has_power or h.backup_power_active or not h.accessible]
        if at_risk_h:
            lines.append("\nHOSPITAL STATUS:")
            for h in at_risk_h:
                lines.append(f"  {h.name}: power={'YES' if h.has_power else 'NO'}, "
                             f"backup={'ACTIVE' if h.backup_power_active else 'off'}, "
                             f"accessible={h.accessible}")

        available = [r for r in state.resources.values() if r.status == "available"]
        en_route = [r for r in state.resources.values() if r.status == "en_route"]
        lines.append(f"\nRESOURCES: {len(available)} available, {len(en_route)} en route")
        for r in available[:5]:
            lines.append(f"  {r.id} ({r.type.value})")

        stranded = sum(1 for v in state.vehicles if v.get("status") == "stranded")
        if stranded:
            lines.append(f"\nVEHICLES: {stranded} stranded in floodwater")

        if state.cascade_chain:
            lines.append("\nCASCADE HISTORY (last 3):")
            for ev in state.cascade_chain[-3:]:
                lines.append(f"  [{ev['severity'].upper()}] {ev['type']}: {ev.get('hospital', ev.get('substation', ''))}")

        return "\n".join(lines)

    def _execute_action(self, action_type: str, params: dict) -> dict:
        engine = self.engine
        dispatch = {
            "reroute_traffic": lambda: engine.reroute_traffic(params.get("zone_id"), params.get("divert_to", [])),
            "dispatch_resource": lambda: engine.dispatch_resource(params.get("resource_id"), params.get("destination_id")),
            "activate_backup_power": lambda: engine.activate_backup_power(params.get("hospital_id")),
            "shed_substation_load": lambda: engine.shed_substation_load(params.get("substation_id"), params.get("target_fraction", 0.7)),
            "begin_evacuation": lambda: engine.begin_evacuation(params.get("zone_id")),
            "reposition_resources_preemptive": lambda: engine.reposition_resources_preemptive(params.get("moves", [])),
            "no_action": lambda: {},
        }
        fn = dispatch.get(action_type)
        return fn() if fn else {"error": f"Unknown action: {action_type}"}

    # Compatibility shim — used by server.py
    @property
    def _pending_approvals_list(self):
        return self._pending_approvals
