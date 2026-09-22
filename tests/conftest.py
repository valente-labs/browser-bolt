"""Every test starts offline, independent of the user's provider credentials."""

import os
from unittest.mock import Mock

import pytest

from jev_ultrafast import model


@pytest.fixture(autouse=True)
def offline_models(monkeypatch):
    prefixes = ("TYPESAFE_", "TEXT_MODEL", "QWEV_", "CEREBRAS_", "OPENROUTER_")
    for name in os.environ:
        if name.startswith(prefixes):
            monkeypatch.delenv(name)
    monkeypatch.setattr(
        model.CLIENT, "post", Mock(side_effect=AssertionError("Live model requests are forbidden in tests"))
    )
