"""
Shared security layer for NEXUS + Rakshak.

Provides:
  - API key authentication (header: X-API-Key)
  - Rate limiting via slowapi
  - Security headers middleware
  - File upload validation
  - Input sanitization helpers
  - WebSocket token check
"""
import os
import re
import time
import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger("rakshak.security")

# ── API Key ───────────────────────────────────────────────────────────────────
# Set NEXUS_API_KEY in your .env.local. If not set, a random key is generated
# on startup and printed once — use it in the X-API-Key header.

_api_key: Optional[str] = None

def get_api_key() -> str:
    global _api_key
    if _api_key is None:
        _api_key = os.getenv("NEXUS_API_KEY")
        if not _api_key:
            import secrets
            _api_key = secrets.token_hex(32)
            logger.warning("=" * 60)
            logger.warning("No NEXUS_API_KEY set. Generated ephemeral key:")
            logger.warning(f"  X-API-Key: {_api_key}")
            logger.warning("Set NEXUS_API_KEY in backend/.env.local to persist.")
            logger.warning("=" * 60)
    return _api_key


def verify_api_key(request: Request) -> bool:
    key = request.headers.get("X-API-Key", "")
    expected = get_api_key()
    # Constant-time compare to prevent timing attacks
    return hashlib.sha256(key.encode()).digest() == hashlib.sha256(expected.encode()).digest()


def require_api_key(request: Request):
    """FastAPI dependency — raises 401 if key missing/wrong."""
    if not verify_api_key(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def verify_ws_token(token: str) -> bool:
    """WebSocket auth — token passed as query param ?token=<key>."""
    expected = get_api_key()
    return hashlib.sha256(token.encode()).digest() == hashlib.sha256(expected.encode()).digest()


# ── Rate limiter ──────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}. Try again later."},
        headers={"Retry-After": "60"},
    )


# ── Security headers middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Cache-Control"] = "no-store"
        # CSP — allow same-origin + ws for WebSocket
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' ws://localhost:8000 ws://localhost:8001 "
            "http://localhost:8000 http://localhost:8001; "
            "img-src 'self' data: blob:; "
            "frame-ancestors 'none';"
        )
        return response


# ── Request size limit middleware ─────────────────────────────────────────────

MAX_REQUEST_SIZE = 600 * 1024 * 1024  # 600 MB (video uploads)
MAX_JSON_SIZE    = 64 * 1024           # 64 KB for JSON bodies

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        content_type   = request.headers.get("content-type", "")

        if content_length:
            size = int(content_length)
            limit = MAX_REQUEST_SIZE if "multipart" in content_type else MAX_JSON_SIZE
            if size > limit:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request too large (max {limit // 1024}KB)"},
                )
        return await call_next(request)


# ── File upload validation ────────────────────────────────────────────────────

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
ALLOWED_VIDEO_MIMETYPES  = {
    "video/mp4", "video/avi", "video/x-msvideo", "video/quicktime",
    "video/x-matroska", "video/webm", "video/x-m4v",
}
MAX_VIDEO_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB

# Magic bytes for common video formats
VIDEO_MAGIC = [
    b"\x00\x00\x00\x18ftyp",   # MP4
    b"\x00\x00\x00\x20ftyp",   # MP4
    b"\x00\x00\x00\x1cftyp",   # MP4
    b"ftyp",                    # MP4/MOV (at offset 4)
    b"RIFF",                    # AVI
    b"\x1a\x45\xdf\xa3",       # MKV/WebM
    b"\x00\x00\x00\x14ftyp",   # MP4
]

def _safe_filename(filename: str) -> str:
    """Strip path components and dangerous characters from filename."""
    # Take only the basename
    name = Path(filename).name
    # Allow only alphanumeric, dash, underscore, dot
    name = re.sub(r"[^\w\-.]", "_", name)
    # Collapse multiple dots to prevent double-extension tricks
    name = re.sub(r"\.{2,}", ".", name)
    # Limit length
    if len(name) > 120:
        stem = Path(name).stem[:100]
        suffix = Path(name).suffix[:10]
        name = stem + suffix
    return name or "upload"


def validate_video_upload(filename: str, content_type: str, file_head: bytes) -> str:
    """
    Validate uploaded file is a real video.
    Returns sanitized filename. Raises HTTPException on failure.
    """
    safe_name = _safe_filename(filename)
    ext = Path(safe_name).suffix.lower()

    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Allowed: {', '.join(ALLOWED_VIDEO_EXTENSIONS)}",
        )

    if content_type and content_type.split(";")[0].strip() not in ALLOWED_VIDEO_MIMETYPES:
        # Some browsers send wrong mimetypes — warn but don't hard-block
        logger.warning(f"Unexpected content-type '{content_type}' for video upload")

    # Magic byte check
    is_video = False
    for magic in VIDEO_MAGIC:
        if file_head.startswith(magic) or magic in file_head[:32]:
            is_video = True
            break
    if not is_video:
        raise HTTPException(
            status_code=400,
            detail="File does not appear to be a valid video (magic byte check failed)",
        )

    return safe_name


# ── Input sanitization ────────────────────────────────────────────────────────

VALID_CAMERA_IDS = {
    "cam_meh_001", "cam_meh_002", "cam_tol_001",
    "cam_nar_001", "cam_mal_001", "cam_lb_001",
}

VALID_ZONE_IDS = {
    "mehdipatnam_up", "tolichowki_up", "narayanguda_up",
    "malakpet_up", "lb_nagar_up",
}

VALID_RESOURCE_IDS = {
    "amb_01", "amb_02", "amb_03", "amb_04",
    "fire_01", "fire_02", "pow_01", "pow_02",
    "pol_01", "pol_02",
}

VALID_HOSPITAL_IDS = {
    "osmania_hospital", "nims", "apollo", "yashoda", "care_hospital",
}

VALID_SUBSTATION_IDS = {
    "sub_mehdipatnam", "sub_banjara", "sub_ameerpet", "sub_lb_nagar",
}

VALID_ACTION_TYPES = {
    "reroute_traffic", "dispatch_resource", "activate_backup_power",
    "shed_substation_load", "begin_evacuation",
    "reposition_resources_preemptive", "no_action",
}

VALID_SCENARIOS = {"normal", "flood_sept2024"}


def validate_camera_id(camera_id: str):
    if camera_id not in VALID_CAMERA_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown camera: {camera_id}")


def validate_action_params(action_type: str, parameters: dict):
    """Validate manual action parameters against known-good values."""
    if action_type not in VALID_ACTION_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown action type: {action_type}")

    p = parameters
    if action_type == "reroute_traffic":
        if p.get("zone_id") not in VALID_ZONE_IDS:
            raise HTTPException(status_code=400, detail="Invalid zone_id")
        divert = p.get("divert_to", [])
        if not isinstance(divert, list) or len(divert) > 5:
            raise HTTPException(status_code=400, detail="Invalid divert_to")
        for z in divert:
            if z not in VALID_ZONE_IDS:
                raise HTTPException(status_code=400, detail=f"Invalid divert zone: {z}")

    elif action_type == "dispatch_resource":
        if p.get("resource_id") not in VALID_RESOURCE_IDS:
            raise HTTPException(status_code=400, detail="Invalid resource_id")
        if p.get("destination_id") not in VALID_ZONE_IDS:
            raise HTTPException(status_code=400, detail="Invalid destination_id")

    elif action_type == "activate_backup_power":
        if p.get("hospital_id") not in VALID_HOSPITAL_IDS:
            raise HTTPException(status_code=400, detail="Invalid hospital_id")

    elif action_type == "shed_substation_load":
        if p.get("substation_id") not in VALID_SUBSTATION_IDS:
            raise HTTPException(status_code=400, detail="Invalid substation_id")
        frac = p.get("target_fraction", 0.7)
        if not isinstance(frac, (int, float)) or not (0.0 <= frac <= 1.0):
            raise HTTPException(status_code=400, detail="target_fraction must be 0.0–1.0")

    elif action_type == "begin_evacuation":
        if p.get("zone_id") not in VALID_ZONE_IDS:
            raise HTTPException(status_code=400, detail="Invalid zone_id")

    elif action_type == "reposition_resources_preemptive":
        moves = p.get("moves", [])
        if not isinstance(moves, list) or len(moves) > 10:
            raise HTTPException(status_code=400, detail="Invalid moves list")
        for m in moves:
            if m.get("resource_id") not in VALID_RESOURCE_IDS:
                raise HTTPException(status_code=400, detail=f"Invalid resource: {m.get('resource_id')}")
            if m.get("destination_id") not in VALID_ZONE_IDS:
                raise HTTPException(status_code=400, detail=f"Invalid destination: {m.get('destination_id')}")


# ── Upload cleanup ────────────────────────────────────────────────────────────

def cleanup_old_uploads(uploads_dir: Path, max_age_hours: int = 24):
    """Delete uploaded video files older than max_age_hours."""
    cutoff = time.time() - (max_age_hours * 3600)
    deleted = 0
    for f in uploads_dir.glob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except Exception as e:
                logger.warning(f"Could not delete old upload {f}: {e}")
    if deleted:
        logger.info(f"Cleaned up {deleted} old upload(s) from {uploads_dir}")
