"""Jednorazowe uzyskanie refresh tokena Gmail (zakres tylko gmail.send).

Uruchamiasz u siebie na komputerze, nie na serwerze. Wystarczy sam Python, bez instalowania pakietów:

    python -m fakturobot.gmail_auth ścieżka/do/client_secret.json

Otworzy się przeglądarka z logowaniem Google. Po zgodzie skrypt wypisze wartości do .env.
Nic nie zapisuje na dysku.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.send"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    with open(argv[1], encoding="utf-8") as f:
        client = json.load(f).get("installed")
    if not client:
        print("To nie jest plik klienta OAuth typu „Aplikacja komputerowa” (brak klucza „installed”).")
        return 1

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            result.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Gotowe, możesz zamknąć tę kartę i wrócić do terminala.".encode())

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)  # tylko lokalnie, na losowym porcie, na czas jednego żądania
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print("Otwieram przeglądarkę. Jeśli się nie otworzy, wejdź na:\n" + url)
    webbrowser.open(url)
    server.handle_request()
    server.server_close()

    if result.get("state") != state or "code" not in result:
        print(f"Logowanie nieudane: {result.get('error', 'niepoprawna odpowiedź')}")
        return 1

    data = urllib.parse.urlencode({
        "code": result["code"],
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data)) as resp:  # noqa: S310 — stały URL Google
        tokens = json.load(resp)
    if "refresh_token" not in tokens:
        print("Google nie zwróciło refresh tokena. Usuń dostęp aplikacji na myaccount.google.com/permissions i spróbuj ponownie.")
        return 1

    print("\nWklej do /etc/fakturobot/.env na serwerze:\n")
    print(f"GOOGLE_CLIENT_ID={client['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={client['client_secret']}")
    print(f"GOOGLE_REFRESH_TOKEN={tokens['refresh_token']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
