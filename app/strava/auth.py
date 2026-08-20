"""Jednorazowa autoryzacja w Stravie.

    python3 -m app.strava.auth              # otwiera lokalny odbiornik na porcie 8721
    python3 -m app.strava.auth --code ABC   # gdy kod skopiowales recznie z URL-a

Po autoryzacji tokeny ladują w tabeli strava_tokens i sa odswiezane automatycznie.
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
import urllib.parse

from app.db import connect, init_db
from app.strava.client import authorize_url, exchange_code

PORT = 8721
REDIRECT = f"http://localhost:{PORT}/exchange_token"

_received: dict[str, str] = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]
        if code:
            _received["code"] = code
            _received["scope"] = (params.get("scope") or [""])[0]
            body = "<h2>Gotowe.</h2><p>Mozesz zamknac to okno i wrocic do terminala.</p>"
        else:
            body = f"<h2>Blad autoryzacji</h2><p>{error or 'brak kodu w odpowiedzi'}</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body style='font-family:sans-serif'>{body}</body></html>"
                         .encode())

    def log_message(self, *args) -> None:  # cisza w konsoli
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Autoryzacja Strava OAuth")
    parser.add_argument("--code", help="kod z parametru ?code= po przekierowaniu")
    args = parser.parse_args()

    init_db()
    conn = connect()

    code = args.code
    if not code:
        url = authorize_url(REDIRECT)
        print("1) Otworz ten adres w przegladarce (na tym samym komputerze):\n")
        print("   " + url + "\n")
        print("2) Zatwierdz dostep. Czekam na przekierowanie...\n")
        print("   Uwaga: w ustawieniach aplikacji na Stravie 'Authorization Callback Domain'")
        print("   musi byc ustawione na: localhost\n")
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", PORT), _Handler) as httpd:
            while "code" not in _received:
                httpd.handle_request()
        code = _received["code"]
        print(f"Odebrano kod (scope: {_received.get('scope')})")

    data = exchange_code(conn, code)
    athlete = data.get("athlete") or {}
    print(f"OK. Zalogowano jako {athlete.get('firstname','')} {athlete.get('lastname','')} "
          f"(athlete_id={athlete.get('id')})")
    print("Powiaz profil z uzytkownikiem:")
    print(f"  python3 manage_users.py link <username> {athlete.get('id')}")
    print("Nastepnie pobierz aktywnosci:  python3 -m app.strava.sync --all")
    conn.close()


if __name__ == "__main__":
    main()
