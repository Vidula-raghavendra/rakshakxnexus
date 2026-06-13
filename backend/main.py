"""Entry point — run with: uvicorn backend.main:app --reload --port 8000"""
from .api.server import app

__all__ = ["app"]
