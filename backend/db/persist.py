"""
Supabase persistence for NEXUS.

Writes AI decisions, Rakshak incidents, and scenario run metadata to
Supabase project zewyxdrqohpxkpmuehpv in real time.

Requires env vars:
  SUPABASE_URL  — https://zewyxdrqohpxkpmuehpv.supabase.co
  SUPABASE_KEY  — anon/publishable key

Tables (created via migration):
  nexus_decisions   — every GovernorDecision with agent votes + counterfactual
  nexus_incidents   — every Rakshak CCTV incident detected
  nexus_scenario_runs — start/end of each flood scenario with summary stats
"""
import os
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("nexus.db")

_client = None
_scenario_run_id: Optional[str] = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        logger.info("[DB] Supabase connected → %s", url)
    except Exception as e:
        logger.warning("[DB] Supabase init failed: %s", e)
        _client = None

    return _client


# ── Scenario runs ─────────────────────────────────────────────────────────────

async def start_scenario_run(scenario: str) -> Optional[str]:
    global _scenario_run_id
    client = _get_client()
    if not client:
        return None

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, lambda: (
            client.table("nexus_scenario_runs")
            .insert({"scenario": scenario, "status": "running"})
            .execute()
        ))
        rows = result.data
        if rows:
            _scenario_run_id = rows[0]["id"]
            logger.info("[DB] Scenario run started: %s", _scenario_run_id)
            return _scenario_run_id
    except Exception as e:
        logger.warning("[DB] start_scenario_run error: %s", e)
    return None


async def end_scenario_run(stats: dict):
    global _scenario_run_id
    client = _get_client()
    if not client or not _scenario_run_id:
        return

    try:
        await asyncio.get_event_loop().run_in_executor(None, lambda: (
            client.table("nexus_scenario_runs")
            .update({"status": "completed", "summary": stats})
            .eq("id", _scenario_run_id)
            .execute()
        ))
    except Exception as e:
        logger.warning("[DB] end_scenario_run error: %s", e)


# ── Decisions ─────────────────────────────────────────────────────────────────

async def save_decision(decision: dict):
    client = _get_client()
    if not client:
        return

    row = {
        "decision_id":        decision.get("id"),
        "scenario_run_id":    _scenario_run_id,
        "tick":               decision.get("tick"),
        "action_type":        decision.get("action_type"),
        "confidence":         decision.get("confidence"),
        "priority":           decision.get("priority"),
        "status":             decision.get("status"),
        "human_readable":     decision.get("human_readable"),
        "governor_reasoning": decision.get("governor_reasoning"),
        "agent_votes":        decision.get("agent_votes", []),
        "conflicts":          decision.get("conflicts", []),
        "counterfactual":     decision.get("counterfactual"),
        "reversible":         decision.get("reversible", True),
        "parameters":         decision.get("parameters", {}),
    }

    try:
        await asyncio.get_event_loop().run_in_executor(None, lambda: (
            client.table("nexus_decisions").insert(row).execute()
        ))
    except Exception as e:
        logger.warning("[DB] save_decision error: %s", e)


# ── Incidents ─────────────────────────────────────────────────────────────────

async def save_incident(incident: dict):
    client = _get_client()
    if not client:
        return

    row = {
        "scenario_run_id": _scenario_run_id,
        "incident_type":   incident.get("incident_type"),
        "camera_id":       incident.get("camera_id"),
        "lat":             incident.get("lat"),
        "lng":             incident.get("lng"),
        "confidence":      incident.get("confidence"),
        "severity":        incident.get("severity"),
        "description":     incident.get("description"),
        "zone_id":         incident.get("zone_id"),
    }

    try:
        await asyncio.get_event_loop().run_in_executor(None, lambda: (
            client.table("nexus_incidents").insert(row).execute()
        ))
    except Exception as e:
        logger.warning("[DB] save_incident error: %s", e)
