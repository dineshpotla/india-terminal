"""
Vercel entrypoint.

Vercel's Python runtime auto-detects a top-level ASGI app named `app` from common entrypoints
like `server.py`. We re-export the FastAPI app from `app/server.py`.
"""

from app.server import app  # noqa: F401

