from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from mcp.server.fastmcp import Context
from starlette.datastructures import Headers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from british_museum_agent.adapters_mcp.client import MCP_INTERNAL_TOKEN_HEADER  # noqa: E402
from british_museum_agent.adapters_mcp.server import (  # noqa: E402
    create_incident,
    get_accessibility_info,
    get_gallery_status,
)
from british_museum_agent.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Prueba local de las tools MCP del British Museum")
    parser.add_argument(
        "tool",
        choices=["get_gallery_status", "get_accessibility_info", "create_incident"],
    )
    parser.add_argument("--gallery-id", default="room-4")
    args = parser.parse_args()

    if args.tool == "get_gallery_status":
        result = get_gallery_status(args.gallery_id)
    elif args.tool == "get_accessibility_info":
        result = get_accessibility_info(args.gallery_id)
    else:
        internal_token = get_settings().mcp_internal_token_value
        if internal_token is None:
            raise SystemExit("MCP_INTERNAL_TOKEN es obligatorio para create_incident")
        request = SimpleNamespace(
            headers=Headers({MCP_INTERNAL_TOKEN_HEADER: internal_token})
        )
        context = Context(request_context=SimpleNamespace(request=request))
        result = create_incident(
            gallery_id=args.gallery_id,
            category="demo",
            description="Incidente creado por la prueba local de la tool MCP.",
            priority="low",
            reported_by="staff@example.com",
            context=context,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
