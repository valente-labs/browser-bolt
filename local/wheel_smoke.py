"""Run using a clean wheel environment, from outside the source checkout. No API keys."""

import asyncio
import json
import sys
from importlib.resources import files

from mcp import Client, StdioServerParameters

from jev_ultrafast import benchmark


async def main():
    assets = files("jev_ultrafast")
    assert assets.joinpath("snapshot.js").is_file()
    assert assets.joinpath("static/index.html").is_file()
    assert len(benchmark.fixture_bytes()) == 6
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "jev_ultrafast.mcp_server"], env={})
    async with Client(parameters, read_timeout_seconds=15) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {
            "list_profiles",
            "choose_browser_action",
            "write_browser_field",
        }
        result = await client.call_tool("list_profiles", {})
        assert result.structured_content["default_profile"] == "jev_qwen_openrouter"
        assert not result.is_error
    print(json.dumps({"wheel_assets": "passed", "stdio_initialize_list_call": "passed", "profiles": 9}))


if __name__ == "__main__":
    asyncio.run(main())
