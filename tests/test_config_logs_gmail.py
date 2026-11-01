import asyncio
import base64
import email
import json
import logging
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from fakturobot.config import ConfigError, load_settings
from fakturobot.gmail import SEND_URL, TOKEN_URL, GmailClient, GmailError
from fakturobot.logs import RedactingFormatter

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def _config_with(tmp_path, env, old, new):
    path = tmp_path / "c.yaml"
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")
    env["FAKTUROBOT_CONFIG"] = str(path)


def test_example_config_loads(settings):
    assert settings.rate > 0
    assert settings.invoice.vat_rate is None
    assert settings.invoice.buyer[0] == "Przykładowa Firma GmbH"
    assert settings.format_number(10, 2026) == "10/2026"
    assert settings.first_seq(2026) == 1 and settings.first_seq(2027) == 1
    assert not settings.dry_run


def test_vat_rate_percent(tmp_path, env):
    _config_with(tmp_path, env, "  vat: zw", "  vat: 23")
    assert load_settings(env).invoice.vat_rate == Decimal("0.23")


def test_dry_run_is_default(env):
    env.pop("DRY_RUN")
    assert load_settings(env).dry_run


def test_zero_rate_rejected(tmp_path, env):
    _config_with(tmp_path, env, 'stawka_godzinowa: "100.00"', 'stawka_godzinowa: "0"')
    with pytest.raises(ConfigError, match="stawka_godzinowa"):
        load_settings(env)


def test_number_format_needs_number(tmp_path, env):
    _config_with(tmp_path, env, 'format: "{numer}/{rok}"', 'format: "FV/{rok}"')
    with pytest.raises(ConfigError, match="numeracja.format"):
        load_settings(env)


def test_accountant_cc(env):
    assert load_settings(env).accountant_cc == ()
    env["ACCOUNTANT_CC"] = "a@example.com, b@example.com"
    assert load_settings(env).accountant_cc == ("a@example.com", "b@example.com")
    env["ACCOUNTANT_CC"] = "a@example.com, nie-adres"
    with pytest.raises(ConfigError, match="ACCOUNTANT_CC"):
        load_settings(env)


def test_partial_gmail_config_rejected(env):
    env.pop("GOOGLE_REFRESH_TOKEN")
    with pytest.raises(ConfigError, match="GOOGLE_REFRESH_TOKEN"):
        load_settings(env)


def test_config_error_does_not_leak_values(env):
    env["TELEGRAM_CHAT_ID"] = "secret-not-a-number"
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert "secret-not-a-number" not in str(exc.value)


def test_redacting_formatter(settings):
    fmt = RedactingFormatter(settings.secrets())
    record = logging.LogRecord("x", logging.INFO, "", 0, "GET https://api.telegram.org/bot%s/getUpdates",
                               (settings.telegram_token,), None)
    out = fmt.format(record)
    assert settings.telegram_token not in out and "***" in out


def test_gmail_send_with_attachment():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert str(request.url) == SEND_URL
        assert request.headers["Authorization"] == "Bearer at"
        seen["raw"] = json.loads(request.content)["raw"]
        return httpx.Response(200, json={"id": "x"})

    client = GmailClient("cid", "secret", "refresh", "ja@example.com",
                         http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(client.send("ksiegowa@example.com", "Faktura listopad 2026", "Dzień dobry", b"%PDF-1.4", "f.pdf",
                            cc=("a@example.com", "b@example.com")))
    msg = email.message_from_bytes(base64.urlsafe_b64decode(seen["raw"]))
    assert msg["To"] == "ksiegowa@example.com"
    assert msg["Cc"] == "a@example.com, b@example.com"
    attachments = [p for p in msg.walk() if p.get_filename()]
    assert attachments[0].get_filename() == "f.pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF-1.4"


def test_gmail_invalid_grant_message():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = GmailClient("cid", "secret", "refresh", "ja@example.com",
                         http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(GmailError, match="wygasł"):
        asyncio.run(client.send("a@example.com", "s", "b"))
