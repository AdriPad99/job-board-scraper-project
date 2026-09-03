"""Shared Google API auth: builds OAuth-refreshed, timed API service clients.

Reused by the Gmail reader (`email_checker`) and the Sheets application tracker
(`sheets_tracker`). A single OAuth client — minted once with get_gmail_token.py —
authorizes BOTH scopes below, so one refresh token covers reading Gmail and
reading/writing the tracker spreadsheet.
"""

import os

from logger import get_logger

logger = get_logger(__name__)

# The single OAuth token requests both scopes: read Gmail, and read/write the
# tracker spreadsheet. Adding the Sheets scope means the token must be re-minted
# (re-run get_gmail_token.py) — an older gmail.readonly-only token can't write.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# The env vars carrying the OAuth refresh credentials (shared by all Google APIs).
_REQUIRED_ENV = (
    "GMAIL_OAUTH_CLIENT_ID",
    "GMAIL_OAUTH_CLIENT_SECRET",
    "GMAIL_OAUTH_REFRESH_TOKEN",
)

# Per-request socket timeout (seconds). httplib2 has NO default timeout, so
# without this a stalled connection hangs the caller forever.
_HTTP_TIMEOUT = 30


class GoogleNotConfigured(RuntimeError):
    """Raised when the Google OAuth env vars aren't all present."""


def _authed_http():
    """Refresh the OAuth credentials and wrap them in a timed HTTP transport.

    Google libraries are imported lazily so importing this module (and the bot)
    doesn't require them until a Google API is actually used.
    """
    missing = [name for name in _REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise GoogleNotConfigured(
            "Google OAuth isn't configured; missing env var(s): " + ", ".join(missing)
            + ". See the README (\"Checking your email\") to set them up."
        )

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import google_auth_httplib2
    import httplib2

    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GMAIL_OAUTH_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_OAUTH_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_OAUTH_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    # Exchange the refresh token for a fresh access token (no browser needed).
    # google-auth's requests transport has a 120s timeout, so this can't hang.
    logger.info("Refreshing Google access token...")
    creds.refresh(Request())

    # AuthorizedHttp attaches the OAuth creds to a timed http; batch/data calls
    # reuse it, so every Google call (token refresh aside) is bounded.
    return google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT)
    )


def build_service(name: str, version: str):
    """Build a timed, OAuth-authorized Google API client (e.g. 'gmail'/'v1',
    'sheets'/'v4'). cache_discovery=False silences a benign file-cache warning."""
    from googleapiclient.discovery import build

    http = _authed_http()
    logger.info("Built Google %s/%s client", name, version)
    return build(name, version, http=http, cache_discovery=False)
