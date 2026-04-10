"""
fix_youtube_oauth.py — Regenerate YouTube OAuth refresh token.

Run:  python3 fix_youtube_oauth.py
It will open a browser tab for Google login. After you approve, it saves
the new refresh token directly into your .env file.

Requirements: YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be set in .env.
Get these from: console.cloud.google.com → Your project → APIs & Services → Credentials → OAuth 2.0 Client IDs
"""

import os
import re
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.parse
import json
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.environ.get("YOUTUBE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
REDIRECT_URI  = "http://localhost:8080/callback"
SCOPES        = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
ENV_PATH      = os.path.join(os.path.dirname(__file__), ".env")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: YOUTUBE_CLIENT_ID and/or YOUTUBE_CLIENT_SECRET are missing from .env")
    print("Get them from: console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client IDs")
    exit(1)

auth_code_holder = []


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback":
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]

            if error:
                body = f"<h2>Error: {error}</h2><p>Check the terminal for details.</p>"
                auth_code_holder.append(None)
            elif code:
                body = "<h2>Authorized! You can close this tab.</h2>"
                auth_code_holder.append(code)
            else:
                body = "<h2>No code received.</h2>"
                auth_code_holder.append(None)

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    def log_message(self, format, *args):
        pass  # silence request logs


def exchange_code_for_tokens(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def write_refresh_token_to_env(refresh_token: str) -> None:
    with open(ENV_PATH, "r") as f:
        content = f.read()

    if re.search(r"^YOUTUBE_REFRESH_TOKEN=", content, re.MULTILINE):
        content = re.sub(
            r"^YOUTUBE_REFRESH_TOKEN=.*$",
            f"YOUTUBE_REFRESH_TOKEN={refresh_token}",
            content,
            flags=re.MULTILINE,
        )
    else:
        content += f"\nYOUTUBE_REFRESH_TOKEN={refresh_token}\n"

    with open(ENV_PATH, "w") as f:
        f.write(content)


def main():
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={urllib.parse.quote(CLIENT_ID)}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        "&response_type=code"
        "&access_type=offline"
        "&prompt=consent"
        f"&scope={urllib.parse.quote(SCOPES)}"
    )

    print("\nStarting local OAuth server on port 8080...")
    print("Opening browser for Google login...\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    print("Waiting for Google to redirect back... (complete the browser login)")
    while not auth_code_holder:
        server.handle_request()
    server.server_close()

    code = auth_code_holder[0]
    if not code:
        print("ERROR: Authorization failed or was denied.")
        exit(1)

    print("Authorization code received. Exchanging for tokens...")
    tokens = exchange_code_for_tokens(code)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh_token in response. Make sure 'prompt=consent' is set.")
        print(f"Response: {tokens}")
        exit(1)

    write_refresh_token_to_env(refresh_token)
    print(f"\nSuccess! Refresh token saved to .env")
    print(f"  YOUTUBE_REFRESH_TOKEN={refresh_token[:20]}...{refresh_token[-6:]}")
    print("\nRun python3 test_connections.py to verify.\n")


if __name__ == "__main__":
    main()
