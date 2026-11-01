"""Wysyłka maila przez Gmail API z zakresem tylko gmail.send (bez SMTP i hasła aplikacji)."""

from __future__ import annotations

import base64
import time
from collections.abc import Sequence
from email.message import EmailMessage

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailError(Exception):
    pass


def build_message(sender: str, to: str, subject: str, body: str,
                  attachment: bytes | None = None, filename: str | None = None,
                  cc: Sequence[str] = ()) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename or "faktura.pdf")
    return msg


class GmailClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, sender: str,
                 *, http: httpx.AsyncClient | None = None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.sender = sender
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._token: str | None = None
        self._token_expires = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires - 60:
            return self._token
        try:
            resp = await self._http.post(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            })
        except httpx.HTTPError as e:
            raise GmailError(f"Brak połączenia z Google ({type(e).__name__})") from None
        data = _json(resp)
        if resp.status_code != 200 or "access_token" not in data:
            if data.get("error") == "invalid_grant":
                raise GmailError("Token Gmail wygasł albo został odwołany. Wygeneruj nowy (README, sekcja Gmail).")
            raise GmailError(f"Google odrzuciło odświeżenie tokena (HTTP {resp.status_code}, {data.get('error', '?')})")
        self._token = data["access_token"]
        self._token_expires = time.monotonic() + float(data.get("expires_in", 3600))
        return self._token

    async def send(self, to: str, subject: str, body: str,
                   attachment: bytes | None = None, filename: str | None = None, cc: Sequence[str] = ()) -> None:
        msg = build_message(self.sender, to, subject, body, attachment, filename, cc)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        token = await self._access_token()
        try:
            resp = await self._http.post(SEND_URL, json={"raw": raw}, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise GmailError(f"Brak połączenia z Gmail API ({type(e).__name__})") from None
        if resp.status_code != 200:
            error = _json(resp).get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            raise GmailError(f"Gmail API odrzuciło wysyłkę (HTTP {resp.status_code}): {detail or 'brak opisu'}")


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
