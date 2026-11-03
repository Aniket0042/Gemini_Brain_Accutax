"""python -m gemini_brain.mcp_server — the MCP connector over Streamable HTTP at MCP_HOST:MCP_PORT/mcp."""
from __future__ import annotations

import logging

from gemini_brain.config.settings import settings
from gemini_brain.mcp_server.server import build_server, transport_security


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    build_server().run(
        "streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path="/mcp",
        stateless_http=True,  # no per-client state here, so any worker or restart serves any request
        transport_security=transport_security(),
    )


if __name__ == "__main__":
    main()
