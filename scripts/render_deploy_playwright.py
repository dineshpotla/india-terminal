#!/usr/bin/env python3
"""
Open Render's "New Web Service" in a real browser window (Chromium via Playwright).

Why this exists: I cannot complete GitHub/Render OAuth for you from Cursor — there is no
shared session. This script automates the *navigation* and keeps cookies in a local profile
so repeat runs skip login when possible.

First-time setup (run on your machine):
  pip install -r requirements-dev.txt
  playwright install chromium

Usage:
  python scripts/render_deploy_playwright.py
  python scripts/render_deploy_playwright.py --blueprint   # opens Blueprint flow instead

After the page loads, you still: pick repo, set build/start commands (see render.yaml).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / ".playwright-render-profile"

WEB_NEW = "https://dashboard.render.com/web/new"
BLUEPRINT_NEW = "https://dashboard.render.com/blueprint/new"


def main() -> None:
    parser = argparse.ArgumentParser(description="Open Render deploy UI with Playwright")
    parser.add_argument(
        "--blueprint",
        action="store_true",
        help="Open New Blueprint (render.yaml) instead of New Web Service",
    )
    args = parser.parse_args()

    url = BLUEPRINT_NEW if args.blueprint else WEB_NEW
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("Launching Chromium (persistent profile at .playwright-render-profile/)…")
    print("If you see Render login, use GitHub — credentials are never read by this script.\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        try:
            page.wait_for_function(
                "() => !window.location.href.includes('/login')",
                timeout=600_000,
            )
        except Exception:
            print("\nTimeout: still on login after 10 minutes. Close the window and retry.")
            context.close()
            sys.exit(1)

        print(
            "\nPast login. In Render:\n"
            "  • Connect the GitHub repo that contains this project.\n"
            "  • If the app is not at repo root, set Root Directory (e.g. india-terminal).\n"
            "  • Build:  pip install --upgrade pip && pip install -r requirements.txt\n"
            "  • Start:  uvicorn app.server:app --host 0.0.0.0 --port $PORT\n"
            "  • Health check path: /health\n"
        )
        if args.blueprint:
            print("  (Blueprint: ensure render.yaml is at the repo root you connect.)\n")

        try:
            input("Press Enter to close the browser…")
        except EOFError:
            pass
        context.close()


if __name__ == "__main__":
    main()
