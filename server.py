"""
server.py — Uvicorn launcher for the Gemini Brain REST API & Swagger service.

Usage:
    python server.py [--host 0.0.0.0] [--port 8000] [--reload]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add src layout to path
src_dir = Path(__file__).parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import uvicorn
from gemini_brain.config.settings import settings


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Gemini Brain REST API & Swagger server.")
    parser.add_argument("--host", default=settings.api_host, help="Host address (default: 0.0.0.0)")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", settings.api_port)),
        help="Port number (default: 8000, or $PORT if set)",
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code changes.")

    args = parser.parse_args()

    print("=" * 70)
    print("GEMINI BRAIN REST API & SWAGGER SERVICE")
    print("=" * 70)
    print(f"  - API Base URL : http://{args.host}:{args.port}")
    print(f"  - Swagger UI   : http://localhost:{args.port}/docs")
    print(f"  - ReDoc Docs   : http://localhost:{args.port}/redoc")
    print(f"  - OpenAPI Spec : http://localhost:{args.port}/openapi.json")
    print("=" * 70 + "\n")

    if args.reload:
        uvicorn.run(
            "gemini_brain.api.app:app",
            host=args.host,
            port=args.port,
            reload=True,
            app_dir=str(src_dir),
        )
    else:
        from gemini_brain.api.app import app
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
        )


if __name__ == "__main__":
    main()
