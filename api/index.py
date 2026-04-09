"""
Vercel Function entrypoint.

Vercel's Python runtime looks for a top-level ASGI app named `app` inside the `api/` directory.
We re-export the FastAPI app defined in `app/server.py`.
"""

from app.server import app  # noqa: F401

