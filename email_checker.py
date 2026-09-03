"""Gmail-backed job-application email triage, decoupled from the Discord bot.

`check_job_emails` pulls the candidate's recent Gmail messages, has Claude
classify each one (application confirmation / rejection / offer / interview
request / not-job-related), and returns a Markdown summary grouped by category.

Read-only: it uses the Gmail API with the gmail.readonly scope and never
modifies the mailbox. Credentials come from three env vars minted once with
get_gmail_token.py (see the README):

    GMAIL_OAUTH_CLIENT_ID
    GMAIL_OAUTH_CLIENT_SECRET
    GMAIL_OAUTH_REFRESH_TOKEN
"""

import os
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from claude import call_claude, EXTRACTION_MODEL
from models import EmailTriage
from prompts import EMAIL_TRIAGE_SYSTEM, EMAIL_TRIAGE_PROMPT
from logger import get_logger

logger = get_logger(__name__)

# Read-only: the bot never sends, deletes, or modifies mail.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# The three env vars that carry the OAuth refresh credentials.
_REQUIRED_ENV = (
    "GMAIL_OAUTH_CLIENT_ID",
    "GMAIL_OAUTH_CLIENT_SECRET",
    "GMAIL_OAUTH_REFRESH_TOKEN",
)

# Cap how many recent messages we pull/classify in one run. Keeps the Gmail
# fetch and the single classification call bounded in time and tokens.
_MAX_MESSAGES = 60

# Max sub-requests per Gmail batch HTTP request (Gmail's own limit is 100). With
# _MAX_MESSAGES=60 a single batch covers everything; chunking keeps it correct if
# the cap is ever raised.
_BATCH_SIZE = 100

# Per-request socket timeout (seconds) for the Gmail HTTP transport. httplib2 has
# NO default timeout, so without this a stalled connection hangs the command
# forever; this makes it fail fast instead.
_HTTP_TIMEOUT = 30

# Human-facing labels + emoji, in the order categories are presented. NOT_JOB is
# intentionally absent — those are dropped from the summary.
_CATEGORY_DISPLAY = {
    "OFFER": "🎉 Offers / acceptances",
    "INTERVIEW": "📅 Interview requests",
    "CONFIRMATION": "✅ Application confirmations",
    "REJECTION": "❌ Rejections",
}


@dataclass
class _Email:
    """A single fetched message reduced to what triage needs."""
    index: int
    sender: str
    subject: str
    date: str
    snippet: str


class GmailNotConfigured(RuntimeError):
    """Raised when the Gmail OAuth env vars aren't all present."""


def _build_service():
    """Build a read-only Gmail API client from the OAuth refresh credentials.

    Imports the Google libraries lazily so importing this module (and the bot)
    doesn't require them until /checkemail is actually used.
    """
    missing = [name for name in _REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise GmailNotConfigured(
            "Gmail isn't configured; missing env var(s): " + ", ".join(missing)
            + ". See the README (\"Checking your email\") to set them up."
        )

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    import google_auth_httplib2
    import httplib2

    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GMAIL_OAUTH_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_OAUTH_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_OAUTH_CLIENT_SECRET"),
        scopes=GMAIL_SCOPES,
    )
    # Exchange the refresh token for a fresh access token (no browser needed).
    # google-auth's requests transport has a 120s timeout, so this can't hang.
    logger.info("Refreshing Gmail access token...")
    creds.refresh(Request())

    # Build the client over an http transport with an explicit per-request socket
    # timeout. AuthorizedHttp attaches the OAuth creds to that timed http, and the
    # batch fetch reuses it — so every Gmail call (token refresh aside) is bounded.
    # cache_discovery=False avoids a noisy warning when no file cache is available.
    authed_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT)
    )
    logger.info("Gmail client ready")
    return build("gmail", "v1", http=authed_http, cache_discovery=False)


def _header(headers: list[dict], name: str) -> str:
    """Case-insensitive lookup of a header value from Gmail's header list."""
    lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == lower:
            return h.get("value", "") or ""
    return ""


def _fetch_recent_emails(service, days: int) -> list[_Email]:
    """List messages from the last `days` days and fetch their metadata + snippet.

    Uses format='metadata' (headers + snippet only) rather than full bodies:
    the snippet is enough to classify without pulling/decoding message payloads,
    which keeps the fetch fast and the classification prompt small.
    """
    # Gmail's default search already excludes Spam and Trash. No category filter,
    # so mail routed to the Updates/Promotions tabs (where many ATS emails land)
    # is still included.
    query = f"newer_than:{days}d"
    logger.info("Listing Gmail messages (query=%r, max=%d)", query, _MAX_MESSAGES)

    listed = service.users().messages().list(
        userId="me", q=query, maxResults=_MAX_MESSAGES
    ).execute()
    message_refs = listed.get("messages", [])
    logger.info("Found %d message(s) in the last %d day(s)", len(message_refs), days)

    if not message_refs:
        return []

    # Fetch every message's metadata in one Gmail batch HTTP request (per chunk)
    # instead of one round trip per message. Batch responses can arrive in any
    # order, so remember each id's original position (Gmail lists newest-first)
    # and reassemble afterward.
    order = {ref["id"]: i for i, ref in enumerate(message_refs)}
    fetched: dict[int, dict] = {}

    def _on_response(request_id, response, exception):
        if exception is not None:
            # Skip a message that failed rather than aborting the whole batch.
            logger.warning("Failed to fetch message %s: %s", request_id, exception)
            return
        headers = response.get("payload", {}).get("headers", [])
        fetched[order[request_id]] = {
            "sender": _header(headers, "From"),
            "subject": _header(headers, "Subject"),
            "date": _header(headers, "Date"),
            "snippet": response.get("snippet", "") or "",
        }

    for start in range(0, len(message_refs), _BATCH_SIZE):
        chunk = message_refs[start:start + _BATCH_SIZE]
        logger.info("Fetching metadata for %d message(s) via batch...", len(chunk))
        batch = service.new_batch_http_request(callback=_on_response)
        for ref in chunk:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ),
                request_id=ref["id"],
            )
        batch.execute()
        logger.info("Batch fetched (%d/%d message(s) so far)", len(fetched), len(message_refs))

    # Rebuild in Gmail's original newest-first order, dropping any that failed,
    # and reindex 0..N-1 so the indices handed to Claude stay contiguous.
    emails: list[_Email] = []
    for position in sorted(fetched):
        item = fetched[position]
        emails.append(
            _Email(
                index=len(emails),
                sender=item["sender"],
                subject=item["subject"],
                date=item["date"],
                snippet=item["snippet"],
            )
        )
    return emails


def _render_emails_for_prompt(emails: list[_Email]) -> str:
    """Render the fetched emails as a compact numbered block for classification."""
    blocks = []
    for e in emails:
        # Snippets are already short (Gmail caps them ~200 chars); trim defensively.
        snippet = e.snippet[:300]
        blocks.append(
            f"[{e.index}] From: {e.sender} | Subject: {e.subject} | Date: {e.date}\n"
            f"Snippet: {snippet}"
        )
    return "\n\n".join(blocks)


def _pretty_date(raw: str) -> str:
    """Best-effort 'YYYY-MM-DD' from an RFC 2822 Date header; raw string on failure."""
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return raw


def check_job_emails(days: int = 7) -> str:
    """Scan the last `days` days of Gmail and summarize job-application activity.

    Returns Markdown grouped into offers, interview requests, confirmations, and
    rejections. Job-board alert/digest emails and other non-application mail are
    classified out. Raises GmailNotConfigured if the OAuth env vars are unset.
    """
    logger.info("Authenticating with Gmail...")
    service = _build_service()
    emails = _fetch_recent_emails(service, days=days)

    if not emails:
        return f"No emails found in the last {days} day(s)."

    logger.info("Classifying %d email(s) with Claude...", len(emails))
    triage = call_claude(
        prompt=EMAIL_TRIAGE_PROMPT.format(emails=_render_emails_for_prompt(emails)),
        history=[],
        model=EmailTriage,
        system=EMAIL_TRIAGE_SYSTEM,
        model_id=EXTRACTION_MODEL,
    )

    # Bucket classified items by category, ignoring NOT_JOB and any out-of-range
    # index the model might return. Pair each back to its source email for the date.
    by_index = {e.index: e for e in emails}
    buckets: dict[str, list[dict]] = {key: [] for key in _CATEGORY_DISPLAY}
    for item in triage.items:
        if item.category not in buckets:
            continue  # NOT_JOB or anything unexpected
        source = by_index.get(item.index)
        if source is None:
            continue
        buckets[item.category].append(
            {
                "company": (item.company or "").strip(),
                "role": (item.role or "").strip(),
                "summary": (item.summary or "").strip(),
                "date": _pretty_date(source.date),
                "subject": source.subject,
            }
        )

    total = sum(len(v) for v in buckets.values())
    logger.info("Triage complete: %d job-related email(s) of %d scanned", total, len(emails))

    return _format_triage_markdown(buckets, days=days, scanned=len(emails), total=total)


def _format_triage_markdown(buckets: dict[str, list[dict]], *, days: int, scanned: int, total: int) -> str:
    """Render the bucketed triage results as a deterministic Markdown summary."""
    lines = [
        f"# Job-application email summary",
        "",
        f"*Scanned {scanned} email(s) from the last {days} day(s); "
        f"{total} related to your applications.*",
    ]

    if total == 0:
        lines += ["", "No application confirmations, rejections, offers, or interview requests found."]
        return "\n".join(lines) + "\n"

    for category, heading in _CATEGORY_DISPLAY.items():
        entries = buckets[category]
        if not entries:
            continue
        lines += ["", "---", "", f"## {heading} ({len(entries)})", ""]
        for entry in entries:
            # Prefer "Company — Role"; fall back to the subject line when neither
            # was identifiable, so every item still has a readable heading.
            company = entry["company"]
            role = entry["role"]
            if company and role:
                title = f"{company} — {role}"
            elif company:
                title = company
            elif role:
                title = role
            else:
                title = entry["subject"] or "(no subject)"

            date = entry["date"]
            lines.append(f"- **{title}**{f' _({date})_' if date else ''}")
            if entry["summary"]:
                lines.append(f"  - {entry['summary']}")

    return "\n".join(lines) + "\n"
