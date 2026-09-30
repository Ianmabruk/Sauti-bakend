#!/usr/bin/env python3
"""Check the local Sauti setup and report what is working.

Run this after pasting a key into .env. It tells you plainly what is
configured, what is not, and what to do next — without ever printing a
credential.

    python scripts/check_setup.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GREEN, YELLOW, RED, DIM, BOLD, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m",
)


def ok(msg: str) -> None:
    print(f"  {GREEN}ok{RESET}    {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}warn{RESET}  {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}fail{RESET}  {msg}")


def hint(msg: str) -> None:
    print(f"        {DIM}{msg}{RESET}")


def main() -> int:
    # Import after path setup so .env is loaded by backend.config.
    from backend.config.settings import Settings
    from backend.integrations.gemini import GeminiClient

    settings = Settings()
    env_file = ROOT / ".env"

    print(f"\n{BOLD}Sauti local setup{RESET}\n")

    # --- .env ---
    if env_file.exists():
        ok(".env exists and is loaded")
    else:
        warn(".env not found")
        hint("cp .env.example .env")

    # --- Gemini ---
    print(f"\n{BOLD}AI engine{RESET}")
    if not settings.gemini_configured:
        bad("No Gemini key")
        hint("Open .env and set GEMINI_API_KEY=your-key")
        hint("Free key: https://aistudio.google.com/apikey")
    else:
        key = settings.gemini_api_key
        masked = f"{key[:4]}...{key[-4:]} (len {len(key)})"
        ok(f"Gemini key present: {masked}")
        print(f"        {DIM}model: {settings.gemini_model}{RESET}")

        import asyncio

        probe = asyncio.run(GeminiClient(settings).health())
        if probe.get("available"):
            ok("Gemini is reachable")
        else:
            bad("Gemini rejected the key or is unreachable")
            reason = probe.get("reason", "")
            if "API key not valid" in reason:
                hint("The key looks invalid. Check you copied the whole thing.")
            elif "quota" in reason.lower() or "429" in str(probe.get("status")):
                hint("Rate limited. Free tier resets; wait a minute and retry.")
            else:
                hint(reason[:100] or "Check the key and your network.")

    # --- search ---
    print(f"\n{BOLD}Web search{RESET}")
    provider = (settings.search_provider or "auto").lower()
    if provider == "stub":
        warn("Using the stub search provider (offline, not the real web)")
        hint("For live results: set SEARCH_PROVIDER=brave and SEARCH_API_KEY=…")
    elif not settings.search_configured:
        warn(f"SEARCH_PROVIDER={provider} but no SEARCH_API_KEY is set")
    else:
        ok(f"Search provider: {provider}")

    # --- database ---
    print(f"\n{BOLD}Database{RESET}")
    try:
        from backend.app import create_app
        from backend.db import db
        from backend.marketplace.models import Product, Vendor

        app = create_app()
        with app.app_context():
            vendors = Vendor.query.count()
            products = Product.query.count()
        if products:
            ok(f"{vendors} vendors, {products} products available to search")
        else:
            warn("Marketplace is empty, so searches return nothing")
            hint("python scripts/seed_marketplace.py")
    except Exception as exc:  # noqa: BLE001
        if "no such table" in str(exc):
            warn("Marketplace tables are missing from this database")
            hint("alembic upgrade head   then   python scripts/seed_marketplace.py")
        else:
            warn(f"Could not read the database: {type(exc).__name__}")

    print(f"\n{BOLD}Next{RESET}")
    if not settings.gemini_configured:
        print("  1. Put your key in .env")
        print("  2. python scripts/check_setup.py")
        print("  3. FLASK_APP=wsgi.py python -m flask run --port 8000")
    else:
        print("  Terminal 1:  FLASK_APP=wsgi.py python -m flask run --port 8000")
        print("  Terminal 2:  cd sautipay && npm run dev")
        print("  Then open http://localhost:5173")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
