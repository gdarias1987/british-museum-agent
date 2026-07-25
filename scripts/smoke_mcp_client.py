from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from british_museum_agent.adapters_mcp.client import MCPMuseumTools  # noqa: E402
from british_museum_agent.domain.models import UserRole  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test for MCP client over streamable HTTP")
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--gallery-id", default="room-4")
    args = parser.parse_args()

    output, call = MCPMuseumTools(args.url).get_gallery_status(
        {"gallery_id": args.gallery_id},
        UserRole.visitor,
    )
    print(json.dumps({"output": output, "tool_call": call.model_dump()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
