#!/usr/bin/env python3
"""One-time helper to mint a Gmail OAuth refresh token for the /checkemail command.

The Discord bot runs headless (no browser), so it authenticates to Gmail with a
long-lived refresh token instead of an interactive login. You generate that token
ONCE on your own machine with this script, then paste the three printed values
into your .env (and into Railway's variables for the deployed bot).

Prerequisites:
  1. In Google Cloud Console, create a project and enable BOTH the "Gmail API"
     and the "Google Sheets API".
  2. Configure an OAuth consent screen (External is fine; add your own Google
     account as a test user so you don't need app verification).
  3. Create an OAuth client ID of type "Desktop app" and download its JSON as
     credentials.json (next to this script).

Then run:
    uv run python get_gmail_token.py            # uses ./credentials.json
    uv run python get_gmail_token.py path/to/credentials.json

A browser window opens for you to approve access. On success the script prints:
    GMAIL_OAUTH_CLIENT_ID=...
    GMAIL_OAUTH_CLIENT_SECRET=...
    GMAIL_OAUTH_REFRESH_TOKEN=...

Copy those three lines into your .env. The token grants read-only Gmail
(gmail.readonly — the bot can never send/delete/modify mail) plus read/write on
your Sheets (spreadsheets — for the application tracker). If you set this up
before the tracker existed, re-run this script so the new Sheets scope is granted.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Single source of truth for the scopes (Gmail read + Sheets read/write), shared
# with the bot so the minted token always matches what the bot requests.
from gapi import SCOPES


def main() -> int:
    creds_path = sys.argv[1] if len(sys.argv) > 1 else "credentials.json"

    try:
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    except FileNotFoundError:
        print(
            f"Could not find OAuth client file '{creds_path}'.\n"
            "Download it from Google Cloud Console (APIs & Services > Credentials >\n"
            "your OAuth 2.0 Client ID of type 'Desktop app' > Download JSON), then\n"
            "save it as credentials.json or pass its path as an argument.",
            file=sys.stderr,
        )
        return 1

    # access_type=offline + prompt=consent forces Google to return a refresh
    # token (it otherwise omits it on repeat authorizations).
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        print(
            "No refresh token was returned. Revoke the app's access at\n"
            "https://myaccount.google.com/permissions and run this script again.",
            file=sys.stderr,
        )
        return 1

    print("\n# Paste these into your .env (and Railway variables):")
    print(f"GMAIL_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GMAIL_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GMAIL_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
