"""Every test runs without the real Anthropic API key and with no route to the
API: the cloud provider is only ever pointed at a stand-in server on
127.0.0.1 (tests/test_llm.py). CI never calls the API, and neither does a
local run with a key in the environment or in .env."""

import os
from pathlib import Path

import pytest

from palmcards import llm


@pytest.fixture(autouse=True)
def no_cloud(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setattr(llm, "ENV_FILE", Path(os.devnull) / "palmcards.env")  # can't be read
