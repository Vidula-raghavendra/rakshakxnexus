"""
NEXUS FastAPI server.
WebSocket /ws/city    — streams city state snapshots every tick.
WebSocket /ws/decisions — streams AI governor decisions + agent votes in real time.
REST endpoints for scenario control and human approvals.
"""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from slowapi.errors import RateLimitExceeded

load_dotenv()
load_dotenv(dotenv_path="backend/.env.local", override=True)
load_dotenv(dotenv_path=".env.local", override=True)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from security import (
    limiter, SecurityHeadersMiddleware, RequestSizeLimitMiddleware,
    require_api_key, verify_ws_token, validate_action_params,
    VALID_SCENARIOS, VALID_ZONE_IDS, VALID_RESOURCE_IDS,
    rate_limit_exceeded_handler,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("nexus.server")

from ..simulation.engine import SimulationEngine
from ..governor.streaming_governor import StreamingGovernor
from ..sensors.rakshak_adapter import RakshakAdapter
from ..db.persist import save_decision, save_incident, start_scenario_run

# ── Shared state ──────────────────────────────────────────────────────────────
engine = SimulationEngine()
governor = StreamingGovernor(engine)
rakshak = RakshakAdapter(engine)

city_ws_clients: Set[WebSocket] = set()
decision_ws_clients: Set[WebSocket] = set()


async def _broadcast(clients: Set[WebSocket], msg: str):
    dead = set()
    for ws in list(clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    clients -= dead


async def broadcast_city_state(state):
    global city_ws_clients
    snapshot = state.to_snapshot()
    await _broadcast(city_ws_clients, json.dumps({"type": "city_state", "data": snapshot}))


async def broadcast_decision(decision: dict):
    global decision_ws_clients, _rules_mode
    if _rules_mode:
        # Simulate what a dumb rules engine would output
        n_critical = sum(
            1 for z in engine.state.zones.values() if z.status.value == "critical"
        )
        rules_result = {
            "type": "rules_engine_result",
            "data": {
                "alerts": n_critical,
                "status": "DEADLOCK — NO RESOLUTION" if n_critical >= 2 else f"{n_critical} ALERT(S) FIRED",
                "reason": (
                    "Multiple zones above threshold simultaneously. "
                    "No routing logic. No priority order. System stalls."
                    if n_critical >= 2
                    else "Threshold breach. Alert dispatched."
                ),
                "resolved": False if n_critical >= 2 else True,
            }
        }
        await _broadcast(decision_ws_clients, json.dumps(rules_result))
        return
    await _broadcast(decision_ws_clients, json.dumps({"type": "decision", "data": decision}))
    asyncio.create_task(save_decision(decision))


async def broadcast_stream_chunk(chunk: dict):
    global decision_ws_clients
    await _broadcast(decision_ws_clients, json.dumps({"type": "stream_chunk", "data": chunk}))


async def on_rakshak_incident(incident):
    global decision_ws_clients
    incident_dict = {
        "incident_type": incident.incident_type.value,
        "lat": incident.lat,
        "lng": incident.lng,
        "confidence": incident.confidence,
        "camera_id": incident.camera_id,
        "severity": incident.severity,
        "description": incident.description,
        "zone_id": incident.zone_id,
        "timestamp": incident.timestamp,
    }
    asyncio.create_task(save_incident(incident_dict))
    await _broadcast(decision_ws_clients, json.dumps({"type": "rakshak_incident", "data": incident_dict}))


async def on_vehicle_incident(incident: dict):
    global decision_ws_clients
    await _broadcast(decision_ws_clients, json.dumps({"type": "vehicle_incident", "data": incident}))


engine.on_tick(broadcast_city_state)
engine.on_vehicle_incident(on_vehicle_incident)
governor.on_decision(broadcast_decision)
governor.on_stream_chunk(broadcast_stream_chunk)
rakshak.on_incident(on_rakshak_incident)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim_task = asyncio.create_task(engine.start())
    gov_task = asyncio.create_task(governor.start())
    rak_task = asyncio.create_task(rakshak.start())
    yield
    engine.stop()
    governor.stop()
    rakshak.stop()
    sim_task.cancel()
    gov_task.cancel()
    rak_task.cancel()


app = FastAPI(title="NEXUS City Governor", lifespan=lifespan, docs_url=None, redoc_url=None)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://localhost:5173",
        "http://127.0.0.1:3000", "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


# ── WebSocket endpoints ───────────────────────────────────────────────────────

@app.websocket("/ws/city")
async def city_websocket(ws: WebSocket, token: str = ""):
    await ws.accept()
    if not verify_ws_token(token):
        await ws.close(code=1008)
        return
    city_ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "city_state",
            "data": engine.state.to_snapshot()
        }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        city_ws_clients.discard(ws)


@app.websocket("/ws/decisions")
async def decisions_websocket(ws: WebSocket, token: str = ""):
    await ws.accept()
    if not verify_ws_token(token):
        await ws.close(code=1008)
        return
    decision_ws_clients.add(ws)
    await ws.send_text(json.dumps({
        "type": "decision_history",
        "data": engine.state.recent_decisions[-20:]
    }))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        decision_ws_clients.discard(ws)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/status")
@limiter.limit("60/minute")
async def get_status(request: Request):
    s = engine.state
    return {
        "tick": s.tick,
        "scenario": s.scenario_active,
        "ws_clients_city": len(city_ws_clients),
        "ws_clients_decisions": len(decision_ws_clients),
        "pending_approvals": len(governor._pending_approvals),
        "governor": "streaming_single_model",
        "infrastructure": {
            "zones": len(s.zones),
            "substations": len(s.substations),
            "hospitals": len(s.hospitals),
            "resources": len(s.resources),
        }
    }


@app.get("/api/state")
async def get_state():
    return engine.state.to_snapshot()


@app.get("/api/infrastructure")
async def get_infrastructure():
    from ..core.city_graph import get_infrastructure
    return get_infrastructure()


@app.get("/api/agents")
async def get_agents():
    return {"governor": "streaming_single_model", "agents": []}


class ScenarioRequest(BaseModel):
    scenario: str


@app.post("/api/scenario")
@limiter.limit("10/minute")
async def set_scenario(request: Request, req: ScenarioRequest, _auth=Depends(require_api_key)):
    if req.scenario not in VALID_SCENARIOS:
        raise HTTPException(status_code=400, detail="Unknown scenario")
    engine.trigger_scenario(req.scenario)
    if req.scenario != "normal":
        asyncio.create_task(start_scenario_run(req.scenario))
    return {"status": "ok", "scenario": req.scenario}


class ApprovalRequest(BaseModel):
    decision_id: str
    approved: bool


@app.post("/api/approve")
@limiter.limit("30/minute")
async def approve_decision(request: Request, req: ApprovalRequest, _auth=Depends(require_api_key)):
    if not req.decision_id or len(req.decision_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid decision_id")
    await governor.approve_decision(req.decision_id, req.approved)
    return {"status": "ok"}


@app.get("/api/pending-approvals")
async def get_pending_approvals():
    return {"pending": governor._pending_approvals}


@app.get("/api/decisions")
async def get_decisions():
    return {"decisions": engine.state.recent_decisions}


class RulesModeRequest(BaseModel):
    enabled: bool

_rules_mode = False   # global toggle — when True, simulate a dumb threshold rules engine


@app.post("/api/rules-mode")
@limiter.limit("10/minute")
async def set_rules_mode(request: Request, req: RulesModeRequest, _auth=Depends(require_api_key)):
    global _rules_mode
    _rules_mode = req.enabled
    # Broadcast so the UI can reflect the state
    await _broadcast(decision_ws_clients, json.dumps({
        "type": "rules_mode",
        "data": {"enabled": _rules_mode},
    }))
    return {"status": "ok", "rules_mode": _rules_mode}


@app.get("/api/rules-mode")
async def get_rules_mode():
    return {"rules_mode": _rules_mode}


@app.post("/api/redteam")
@limiter.limit("5/minute")
async def red_team(request: Request, _auth=Depends(require_api_key)):
    """
    Inject a novel cross-type cascade: accident → flood → crowd surge → stampede threat.
    Agents receive only the resulting state — no script, no foreknowledge.
    """
    result = engine.inject_red_team_cascade()
    # Force the governor to act immediately on this novel state
    asyncio.create_task(governor._govern())
    return {"status": "injected", "result": result}


@app.post("/api/counterfactual")
@limiter.limit("5/minute")
async def get_counterfactual(request: Request, _auth=Depends(require_api_key)):
    """Run the simulation forward 8 ticks with no intervention and return the divergence."""
    result = engine.run_counterfactual(future_ticks=8)
    return result


class ManualAction(BaseModel):
    action_type: str
    parameters: dict


@app.post("/api/manual-action")
@limiter.limit("20/minute")
async def manual_action(request: Request, req: ManualAction, _auth=Depends(require_api_key)):
    validate_action_params(req.action_type, req.parameters)
    dispatch = {
        "reroute_traffic": lambda: engine.reroute_traffic(
            req.parameters.get("zone_id"), req.parameters.get("divert_to", [])),
        "dispatch_resource": lambda: engine.dispatch_resource(
            req.parameters.get("resource_id"), req.parameters.get("destination_id")),
        "activate_backup_power": lambda: engine.activate_backup_power(
            req.parameters.get("hospital_id")),
        "begin_evacuation": lambda: engine.begin_evacuation(
            req.parameters.get("zone_id")),
    }
    fn = dispatch.get(req.action_type)
    result = fn() if fn else None
    engine.state.recent_decisions.append({
        "id": f"manual_{int(time.time())}",
        "tick": engine.state.tick,
        "action_type": req.action_type,
        "confidence": 1.0,
        "priority": "high",
        "status": "manual_executed",
        "human_readable": f"Manual override: {req.action_type}",
        "reasoning": "Operator direct action",
        "agent_votes": [],
        "conflicts": [],
        "reversible": True,
        "parameters": req.parameters,
    })
    return {"status": "ok", "result": result}
