from __future__ import annotations

from pathlib import Path

import pytest

from fakturobot.config import Settings, load_settings

ROOT = Path(__file__).resolve().parent.parent

FAKE_ENV = {
    "FAKTUROBOT_CONFIG": str(ROOT / "config.example.yaml"),
    "TELEGRAM_BOT_TOKEN": "000000:fake-telegram-token",
    "TELEGRAM_CHAT_ID": "111",
    "DRY_RUN": "false",
    "GMAIL_SENDER": "nadawca@example.com",
    "GOOGLE_CLIENT_ID": "fake-client.apps.example.com",
    "GOOGLE_CLIENT_SECRET": "fake-client-secret",
    "GOOGLE_REFRESH_TOKEN": "fake-refresh-token",
    "ACCOUNTANT_EMAIL": "ksiegowa@example.com",
}


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {**FAKE_ENV, "FAKTUROBOT_DATA_DIR": str(tmp_path)}


@pytest.fixture
def settings(env: dict[str, str]) -> Settings:
    return load_settings(env)
