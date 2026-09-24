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
        assert not result.is_error
        content = result.structured_content
        assert content["default_profile"] == "jev_qwen_openrouter"
        profiles = {item["profile"]: item for item in content["profiles"]}
        assert len(profiles) == len(content["profiles"]) == 10
        assert profiles["jev_qwen"]["required_environment"] == ["OPENROUTER_API_KEY", "CEREBRAS_API_KEY"]
        assert profiles["jev_qwen_openrouter"]["required_environment"] == ["OPENROUTER_API_KEY"]
    print(json.dumps({"wheel_assets": "passed", "stdio_initialize_list_call": "passed", "profiles": 10}))


if __name__ == "__main__":
    asyncio.run(main())
