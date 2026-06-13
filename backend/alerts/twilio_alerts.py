"""
NEXUS Emergency Alert System — Twilio calls + SMS.

Incident routing:
  road_accident, fight_violence, women_safety, crowd_surge  → Police + Ambulance
  road_accident (any)                                        → Ambulance always
  road_blocked, building_damage, garbage_dumping,
  abandoned_object, traffic_signal_issue                    → GHMC
  animal_on_road                                            → GHMC
  vehicle_stranded, road_flood                              → Police + GHMC

Each incident triggers:
  1. A voice call to the primary department (TwiML says what happened)
  2. An SMS with location, camera, confidence, and description
Cooldown: 5 minutes per (incident_type + department) to avoid spam.
"""
import os
import time
import logging
import asyncio
from typing import Optional
from functools import lru_cache

logger = logging.getLogger("rakshak.alerts")

# ── Department config ─────────────────────────────────────────────────────────

DEPARTMENTS = {
    "ambulance": {
        "name": "Ambulance / Hospital",
        "number": None,  # loaded from env
        "env_key": "ALERT_AMBULANCE",
    },
    "police": {
        "name": "Hyderabad Police",
        "number": None,
        "env_key": "ALERT_POLICE",
    },
    "ghmc": {
        "name": "GHMC Control Room",
        "number": None,
        "env_key": "ALERT_GHMC",
    },
}

# Incident type → which departments to alert
INCIDENT_ROUTING: dict[str, list[str]] = {
    "road_accident":        ["police", "ambulance"],
    "fight_violence":       ["police", "ambulance"],
    "women_safety":         ["police"],
    "crowd_surge":          ["police", "ambulance"],
    "road_blocked":         ["police", "ghmc"],
    "building_damage":      ["ghmc", "ambulance"],
    "garbage_dumping":      ["ghmc"],
    "abandoned_object":     ["police"],
    "traffic_signal_issue": ["ghmc", "police"],
    "animal_on_road":       ["ghmc"],
    "vehicle_stranded":     ["police", "ghmc"],
    "road_flood":           ["police", "ghmc"],
}

# ── Cooldown tracker ──────────────────────────────────────────────────────────

_last_alert: dict[str, float] = {}  # key: "incident_type:department"
ALERT_COOLDOWN = 3600  # 1 hour per incident-type per department
MAX_CALLS_PER_SESSION = 1  # one call per upload session
_session_calls_made = 0     # resets when a new video is uploaded
_session_active = False     # True between upload and stop-all
_muted = False              # operator-controlled mute from UI


def _cooldown_key(incident_type: str, dept: str) -> str:
    return f"{incident_type}:{dept}"


def reset_session_alerts():
    """Call when a new video upload starts — allows exactly one call for the new session."""
    global _session_calls_made, _session_active, _last_alert
    _session_calls_made = 0
    _session_active = True
    _last_alert.clear()
    logger.info("Alert session reset — one call allowed for new upload")


def end_session_alerts():
    """Call when stop-all is triggered — blocks further calls until next upload."""
    global _session_active
    _session_active = False
    logger.info("Alert session ended — calls suppressed until next upload")


def mute_alerts():
    """Operator pressed the mute button in the UI — stop all further calls/SMS."""
    global _muted
    _muted = True
    logger.info("Alerts MUTED by operator")


def unmute_alerts():
    """Operator re-enabled calls from the UI."""
    global _muted
    _muted = False
    logger.info("Alerts UNMUTED by operator")


def is_muted() -> bool:
    return _muted


def _is_on_cooldown(incident_type: str, dept: str) -> bool:
    global _session_calls_made
    if _muted:
        logger.info("Alerts muted by operator — suppressing")
        return True
    if not _session_active:
        logger.info("No active session — suppressing alert")
        return True
    if _session_calls_made >= MAX_CALLS_PER_SESSION:
        logger.info(f"Session call cap ({MAX_CALLS_PER_SESSION}) reached — suppressing alert")
        return True
    key = _cooldown_key(incident_type, dept)
    last = _last_alert.get(key, 0.0)
    return time.time() - last < ALERT_COOLDOWN


def _mark_sent(incident_type: str, dept: str):
    global _session_calls_made
    _last_alert[_cooldown_key(incident_type, dept)] = time.time()
    _session_calls_made += 1


# ── Twilio client (lazy init) ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_twilio_client():
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")
    return Client(sid, token)


def _get_from_number() -> str:
    n = os.getenv("TWILIO_FROM_NUMBER")
    if not n:
        raise RuntimeError("TWILIO_FROM_NUMBER not set")
    return n


def _get_dept_number(dept_key: str) -> Optional[str]:
    cfg = DEPARTMENTS.get(dept_key)
    if not cfg:
        return None
    if cfg["number"] is None:
        cfg["number"] = os.getenv(cfg["env_key"])
    return cfg["number"]


# ── Message builders ──────────────────────────────────────────────────────────

def _build_sms(incident: dict, dept_name: str) -> str:
    itype    = incident.get("incident_type", "incident").replace("_", " ").upper()
    severity = incident.get("severity", "high").upper()
    camera   = incident.get("camera_id", "unknown").upper()
    conf     = int(incident.get("confidence", 0.8) * 100)
    desc     = incident.get("description", "")
    zone     = (incident.get("zone_id") or "").replace("_", " ").title()
    lat      = incident.get("lat", 0)
    lng      = incident.get("lng", 0)

    lines = [
        f"[RAKSHAK ALERT] {itype} — {severity}",
        f"Camera: {camera} | Zone: {zone or 'Unknown'}",
        f"Confidence: {conf}%",
        f"Details: {desc}",
        f"Location: https://maps.google.com/?q={lat},{lng}",
        f"Action required by: {dept_name}",
        "— Rakshak CCTV Incident Intelligence, Hyderabad",
    ]
    return "\n".join(lines)


def _build_twiml(incident: dict, dept_name: str, dept_key: str = "") -> str:
    """TwiML spoken when the call is answered. Message differs per department."""
    itype    = incident.get("incident_type", "incident").replace("_", " ")
    severity = incident.get("severity", "high")
    zone     = (incident.get("zone_id") or "unknown zone").replace("_", " ")
    camera   = incident.get("camera_id", "unknown").replace("cam_", "").upper().replace("_", "-")
    conf     = int(incident.get("confidence", 0.8) * 100)

    if dept_key == "ambulance":
        message = (
            f"This is Rakshak, Hyderabad CCTV Emergency Alert. "
            f"A {itype} has been detected in the {zone} area, camera {camera}. "
            f"There may be injured passengers at the scene. "
            f"Please dispatch an ambulance immediately. "
            f"Severity is {severity}. Detection confidence {conf} percent. "
            f"This message will repeat."
        )
    elif dept_key == "police":
        message = (
            f"This is Rakshak, Hyderabad CCTV Emergency Alert. "
            f"A {itype} has been detected in the {zone} area, camera {camera}. "
            f"A tow truck and traffic officers are needed at the scene. "
            f"Please respond immediately and arrange vehicle recovery. "
            f"Severity is {severity}. Detection confidence {conf} percent. "
            f"This message will repeat."
        )
    else:
        message = (
            f"This is Rakshak, Hyderabad CCTV Emergency Alert. "
            f"A {severity} severity {itype} has been detected by camera {camera} "
            f"in the {zone} area. "
            f"Immediate response is requested from {dept_name}. "
            f"Detection confidence {conf} percent. "
            f"This message will repeat."
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="en-IN">{message}</Say>
  <Pause length="1"/>
  <Say voice="alice" language="en-IN">{message}</Say>
</Response>"""


# ── Governor action alert builders ───────────────────────────────────────────

ACTION_ROUTING: dict[str, list[str]] = {
    "reroute_traffic":                 ["police"],
    "dispatch_resource":               ["police", "ambulance"],
    "activate_backup_power":           ["ambulance"],
    "begin_evacuation":                ["police", "ambulance"],
    "shed_substation_load":            ["police"],
    "reposition_resources_preemptive": ["police"],
}

ACTION_READABLE: dict[str, str] = {
    "reroute_traffic":                 "reroute traffic away from the incident zone",
    "dispatch_resource":               "dispatch an emergency resource to the incident location",
    "activate_backup_power":           "activate backup power at the hospital",
    "begin_evacuation":                "begin evacuation of the affected zone",
    "shed_substation_load":            "reduce substation load to prevent grid failure",
    "reposition_resources_preemptive": "reposition emergency units to standby positions",
}

def _build_action_sms(decision: dict, dept_name: str) -> str:
    action  = decision.get("action_type", "action").replace("_", " ").upper()
    human   = decision.get("human_readable", "")
    conf    = int(decision.get("confidence", 0.8) * 100)
    trigger = decision.get("trigger_incident", {})
    itype   = trigger.get("incident_type", "incident").replace("_", " ").upper()
    zone    = (trigger.get("zone_id") or decision.get("parameters", {}).get("zone_id") or "").replace("_", " ").title()
    lat     = trigger.get("lat", 0)
    lng     = trigger.get("lng", 0)

    lines = [
        f"[RAKSHAK AI ACTION] {action}",
        f"AI Confidence: {conf}% | Triggered by: {itype}",
        f"Zone: {zone or 'Unknown'}",
        f"Instruction: {human}",
        f"Location: https://maps.google.com/?q={lat},{lng}" if lat else "",
        f"Directed to: {dept_name}",
        "— Rakshak AI Governor, Hyderabad",
    ]
    return "\n".join(l for l in lines if l)


def _build_action_twiml(decision: dict, dept_name: str, dept_key: str = "") -> str:
    action      = ACTION_READABLE.get(decision.get("action_type", ""), decision.get("action_type", "take action").replace("_", " "))
    human       = decision.get("human_readable", "")
    conf        = int(decision.get("confidence", 0.8) * 100)
    trigger     = decision.get("trigger_incident", {})
    itype       = trigger.get("incident_type", "incident").replace("_", " ")
    zone        = (trigger.get("zone_id") or decision.get("parameters", {}).get("zone_id") or "the affected area").replace("_", " ")

    message = (
        f"This is Rakshak, the AI governance system for Hyderabad. "
        f"A {itype} has been detected in the {zone} area. "
        f"The AI has decided to {action}. "
        f"{human}. "
        f"AI confidence is {conf} percent. "
        f"This action is directed to {dept_name}. "
        f"Please respond immediately. This message will repeat."
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="en-IN">{message}</Say>
  <Pause length="1"/>
  <Say voice="alice" language="en-IN">{message}</Say>
</Response>"""


async def dispatch_action_alerts(decision: dict) -> list[dict]:
    """
    Called when the governor commits to an action.
    Calls + SMSes the relevant departments. Subject to same mute/session cap.
    """
    action_type = decision.get("action_type", "")
    if action_type in ("no_action", ""):
        return []

    if _muted:
        logger.info(f"Action alert muted by operator: {action_type}")
        return []

    departments = ACTION_ROUTING.get(action_type, ["police"])
    loop = asyncio.get_event_loop()
    results = []

    cooldown_key = f"action:{action_type}"
    if _session_calls_made >= MAX_CALLS_PER_SESSION:
        logger.info(f"Session call cap reached — suppressing action alert: {action_type}")
        return []
    if time.time() - _last_alert.get(cooldown_key, 0) < ALERT_COOLDOWN:
        logger.info(f"Action alert suppressed (cooldown): {action_type}")
        return []
    _last_alert[cooldown_key] = time.time()

    for dept_key in departments:
        to_number = _get_dept_number(dept_key)
        if not to_number:
            results.append({"dept": dept_key, "status": "skipped", "reason": "number not configured"})
            continue

        dept_name  = DEPARTMENTS[dept_key]["name"]
        sms_body   = _build_action_sms(decision, dept_name)
        twiml      = _build_action_twiml(decision, dept_name, dept_key)
        result     = {"dept": dept_key, "dept_name": dept_name, "to": to_number}

        try:
            await loop.run_in_executor(None, _send_sms_sync, to_number, sms_body)
            result["sms"] = "sent"
        except Exception as e:
            logger.error(f"Action SMS failed to {dept_key}: {e}")
            result["sms"] = f"failed: {e}"

        try:
            await loop.run_in_executor(None, _send_call_sync, to_number, twiml)
            result["call"] = "initiated"
        except Exception as e:
            logger.error(f"Action call failed to {dept_key}: {e}")
            result["call"] = f"failed: {e}"

        results.append(result)
        logger.info(f"Action alert sent to {dept_key} for decision: {action_type}")

    return results


# ── Core send functions ───────────────────────────────────────────────────────

def _send_sms_sync(to: str, body: str):
    client = _get_twilio_client()
    msg = client.messages.create(
        body=body,
        from_=_get_from_number(),
        to=to,
    )
    logger.info(f"SMS sent to {to}: SID={msg.sid}")


def _send_call_sync(to: str, twiml: str):
    client = _get_twilio_client()
    # Use TwiML bin inline via URL-encoded TwiML
    from twilio.twiml.voice_response import VoiceResponse
    call = client.calls.create(
        twiml=twiml,
        from_=_get_from_number(),
        to=to,
    )
    logger.info(f"Call initiated to {to}: SID={call.sid}")


# ── Public async interface ────────────────────────────────────────────────────

async def dispatch_alerts(incident: dict, bypass_cap: bool = False) -> list[dict]:
    """
    Send calls + SMS to all relevant departments for this incident.
    One call per session max; operator can mute via UI.
    """
    incident_type = incident.get("incident_type", "")
    departments   = INCIDENT_ROUTING.get(incident_type, [])

    if not departments:
        logger.debug(f"No routing for incident type: {incident_type}")
        return []

    loop = asyncio.get_event_loop()
    results = []

    for dept_key in departments:
        if not bypass_cap and _is_on_cooldown(incident_type, dept_key):
            logger.info(f"Alert suppressed (cooldown): {incident_type} → {dept_key}")
            continue

        to_number = _get_dept_number(dept_key)
        if not to_number:
            logger.warning(f"No number configured for {dept_key} — skipping")
            results.append({
                "dept": dept_key,
                "status": "skipped",
                "reason": "number not configured or not verified",
            })
            continue

        dept_name = DEPARTMENTS[dept_key]["name"]
        sms_body  = _build_sms(incident, dept_name)
        twiml     = _build_twiml(incident, dept_name, dept_key)

        result = {"dept": dept_key, "dept_name": dept_name, "to": to_number}

        # SMS — skip silently if it fails (India DLT filtering blocks trial SMS)
        try:
            await loop.run_in_executor(None, _send_sms_sync, to_number, sms_body)
            result["sms"] = "sent"
        except Exception as e:
            logger.warning(f"SMS skipped for {dept_key}: {e}")
            result["sms"] = "skipped"

        # Make call — this is the primary alert mechanism
        try:
            await loop.run_in_executor(None, _send_call_sync, to_number, twiml)
            result["call"] = "initiated"
        except Exception as e:
            logger.error(f"Call failed to {dept_key} ({to_number}): {e}")
            result["call"] = f"failed: {e}"

        _mark_sent(incident_type, dept_key)
        results.append(result)
        logger.info(f"Alerted {dept_key} ({to_number}) for {incident_type}")

    return results
