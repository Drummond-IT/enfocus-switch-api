from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fake_switch import FakeSwitch
from switch_mcp.client import SwitchClient
from switch_mcp.config import Settings
from switch_mcp.server import build_server


@pytest.fixture
def fake() -> FakeSwitch:
    return FakeSwitch()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    return Settings(url="http://switch.test:51088", username="demo", password="demo",
                    upload_dirs=[uploads], download_dir=tmp_path / "downloads")


@pytest.fixture
def client(settings: Settings, fake: FakeSwitch) -> SwitchClient:
    return SwitchClient(settings, transport=httpx.MockTransport(fake))


@pytest.fixture
def make_server(settings: Settings, fake: FakeSwitch):
    def _make(**overrides):
        s = Settings(**{**settings.__dict__, **overrides})
        return build_server(s, SwitchClient(s, transport=httpx.MockTransport(fake)))
    return _make
