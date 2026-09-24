"""Offline check of the public installed-wheel smoke contract."""

import asyncio
import json

from local import wheel_smoke


def test_wheel_smoke_lists_direct_default_without_provider_calls(capsys):
    asyncio.run(wheel_smoke.main())
    result = json.loads(capsys.readouterr().out)
    assert result == {"wheel_assets": "passed", "stdio_initialize_list_call": "passed", "profiles": 10}
