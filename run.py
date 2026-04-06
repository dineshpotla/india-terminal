#!/usr/bin/env python3
"""India Market Terminal — Bloomberg-style terminal for Indian stock markets."""

import uvicorn
import webbrowser
import threading
import time


def open_browser():
    time.sleep(2)
    webbrowser.open("http://localhost:8000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    print("\n  ╔══════════════════════════════════════════╗")
    print("  ║   INDIA MARKET TERMINAL                  ║")
    print("  ║   http://localhost:8000                   ║")
    print("  ╚══════════════════════════════════════════╝\n")
    uvicorn.run("app.server:app", host="0.0.0.0", port=8000, reload=False)
