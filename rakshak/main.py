"""Entry point — run with: uvicorn rakshak.main:app --port 8001"""
from .api.server import app

__all__ = ["app"]
