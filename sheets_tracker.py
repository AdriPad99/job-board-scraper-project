"""Google Sheets application tracker: one row per job application.

`sync_applications` takes the job-related emails from a /checkemail run and
upserts them into a Google Sheet, keyed on **(company, role)**: a brand-new
application appends a row; a follow-up email (e.g. a rejection after a
confirmation) updates the existing row's status/summary/dates in place. Emails
are deduped by Gmail message ID, so re-running /checkemail never double-counts
or double-writes.

The user-maintained **Notes** column is always preserved. A trailing internal
**Message IDs** column stores which messages have already been folded into each
row (the dedup ledger) — you can hide it in the sheet.

Requires:
    GOOGLE_SHEET_ID   — the id in the sheet's URL (…/spreadsheets/d/<ID>/edit)
    GOOGLE_SHEET_TAB  — optional worksheet/tab name (default "Applications")
and the spreadsheets OAuth scope (see gapi.SCOPES; re-mint the token if you
added it after first setup).
"""

import os
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from gapi import build_service
from logger import get_logger

logger = get_logger(__name__)

SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Applications")

# Column order written to the sheet. Everything before the trailing "Message IDs"
# column is user-facing; that last column is internal dedup bookkeeping.
HEADERS = [
    "Company",
    "Role",
    "Status",
    "Date applied",
    "Last update",
    "Latest email category",
    "Latest email summary",
    "From",
    "Subject",
    "Gmail link",
    "# of emails",
    "Notes",
    "Message IDs (internal)",
]
_NOTES = "Notes"
_MSGIDS = "Message IDs (internal)"

# How each classification maps onto the human-readable Status column.
_STATUS_BY_CATEGORY = {
    "CONFIRMATION": "Confirmed",
    "INTERVIEW": "Interviewing",
    "OFFER": "Offer",
    "REJECTION": "Rejected",
}

# Background color per Status for the conditional-formatting rules on the Status
# column. Soft pastels so the dark cell text stays readable. RGB as 0..1 floats
# (Google Sheets' color format).
_STATUS_COLORS = {
    "Offer":        {"red": 0.72, "green": 0.88, "blue": 0.72},  # green
    "Interviewing": {"red": 0.79, "green": 0.87, "blue": 0.97},  # blue
    "Confirmed":    {"red": 1.00, "green": 0.90, "blue": 0.66},  # amber
    "Rejected":     {"red": 0.96, "green": 0.80, "blue": 0.80},  # red
}
_STATUS_COL = HEADERS.index("Status")  # 0-based column of the Status cell


class SheetNotConfigured(RuntimeError):
    """Raised when GOOGLE_SHEET_ID isn't set (the tracker is simply disabled)."""


# ---- small helpers -------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase + collapse whitespace, for stable matching keys."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _sender_domain(sender: str) -> str:
    """Domain portion of a From header, e.g. 'jobs@acme.com' -> 'acme.com'."""
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else ""


def _effective_company(company: str, sender: str) -> str:
    """The company we key/display on: the parsed company, or the sender domain
    when the classifier couldn't name one (keeps roleless mail from merging into
    a single junk row)."""
    return (company or "").strip() or _sender_domain(sender)


def _app_key(company: str, role: str) -> str:
    """Matching key for an application row. `company` must already be the
    effective company (see _effective_company) so a row read back from the sheet
    produces the same key it was written with."""
    return f"{_norm(company)}|{_norm(role)}"


def _to_date(value: str):
    """Parse 'YYYY-MM-DD' or an RFC 2822 Date header to a date; None on failure."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError):
            return None


def _entry_date(entry: dict):
    """Best date for an email entry: its normalized date, else the raw header."""
    return _to_date(entry.get("date")) or _to_date(entry.get("raw_date"))


def _gmail_link(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}" if message_id else ""


def _col_letter(n: int) -> str:
    """1-indexed column number -> A1 letter (enough for our column count)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# ---- sheet I/O -----------------------------------------------------------

def _ensure_tab(service, sheet_id: str) -> None:
    """Create the worksheet/tab if needed, then ensure the Status color rules."""
    meta = service.spreadsheets().get(
        spreadsheetId=sheet_id,
        fields="sheets(properties(sheetId,title),conditionalFormats)",
    ).execute()

    tab = next(
        (s for s in meta.get("sheets", []) if s["properties"]["title"] == SHEET_TAB),
        None,
    )
    if tab is None:
        logger.info("Creating missing tab %r in spreadsheet", SHEET_TAB)
        reply = service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TAB}}}]},
        ).execute()
        tab_id = reply["replies"][0]["addSheet"]["properties"]["sheetId"]
        existing_formats = []
    else:
        tab_id = tab["properties"]["sheetId"]
        existing_formats = tab.get("conditionalFormats", [])

    _ensure_status_colors(service, sheet_id, tab_id, existing_formats)


def _ensure_status_colors(service, sheet_id: str, tab_id: int, existing_formats: list) -> None:
    """Install a conditional-format rule per Status value, once (idempotent).

    Each rule shades the Status column where the text equals a status. We only add
    rules for statuses not already covered, so re-runs never pile up duplicates —
    and the color then tracks each cell's value automatically, including on rows
    edited by hand or added later.
    """
    # Statuses that already have a TEXT_EQ rule (from a prior run).
    covered = set()
    for fmt in existing_formats:
        cond = fmt.get("booleanRule", {}).get("condition", {})
        if cond.get("type") == "TEXT_EQ":
            for v in cond.get("values", []):
                covered.add(v.get("userEnteredValue"))

    requests = []
    for status, color in _STATUS_COLORS.items():
        if status in covered:
            continue
        requests.append({
            "addConditionalFormatRule": {
                "index": 0,
                "rule": {
                    "ranges": [{
                        "sheetId": tab_id,
                        "startRowIndex": 1,  # skip the header row
                        "startColumnIndex": _STATUS_COL,
                        "endColumnIndex": _STATUS_COL + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": status}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
            }
        })

    if requests:
        logger.info("Adding %d Status color rule(s) to the tracker", len(requests))
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute()


def _read_rows(service, sheet_id: str) -> tuple[list[dict], bool]:
    """Read the tab's values into row dicts. Returns (rows, header_present)."""
    last_col = _col_letter(len(HEADERS))
    values = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{SHEET_TAB}!A1:{last_col}"
    ).execute().get("values", [])

    if not values:
        return [], False

    header_present = values[0][:1] == [HEADERS[0]]
    data = values[1:] if header_present else values
    rows = []
    for raw in data:
        # Pad short rows (Sheets omits trailing empties) to a full-width dict.
        padded = list(raw) + [""] * (len(HEADERS) - len(raw))
        rows.append({h: padded[i] for i, h in enumerate(HEADERS)})
    return rows, header_present


def _write_table(service, sheet_id: str, rows: list[dict]) -> None:
    """Overwrite the tab with the header + given rows (one Sheets call)."""
    table = [HEADERS] + [[row.get(h, "") for h in HEADERS] for row in rows]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{SHEET_TAB}!A1",
        valueInputOption="RAW",
        body={"values": table},
    ).execute()


# ---- merge logic ---------------------------------------------------------

def _msg_ids(row: dict) -> set[str]:
    return {mid for mid in re.split(r"[,\s]+", row.get(_MSGIDS, "")) if mid}


def _apply_latest(row: dict, entry: dict) -> None:
    """Set the 'latest email' fields of a row from a single email entry."""
    row["Status"] = _STATUS_BY_CATEGORY.get(entry["category"], entry["category"].title())
    row["Latest email category"] = entry["category"]
    row["Latest email summary"] = entry.get("summary", "")
    row["From"] = entry.get("sender", "")
    row["Subject"] = entry.get("subject", "")
    row["Gmail link"] = _gmail_link(entry.get("message_id", ""))


def _fold(row: dict, entries: list[dict], *, is_new: bool) -> None:
    """Fold `entries` (new, unseen emails for this application) into `row`.

    Updates the aggregate fields: earliest 'Date applied', latest 'Last update',
    email count, the dedup ledger, and — only if these emails are at least as
    recent as what's already recorded — the 'latest email' fields.
    """
    dated = [(e, _entry_date(e)) for e in entries]
    real_dates = [d for _, d in dated if d is not None]

    # Earliest applied / latest update across old + new.
    existing_applied = _to_date(row.get("Date applied"))
    existing_update = _to_date(row.get("Last update"))
    applied_candidates = ([existing_applied] if existing_applied else []) + real_dates
    update_candidates = ([existing_update] if existing_update else []) + real_dates
    if applied_candidates:
        row["Date applied"] = min(applied_candidates).isoformat()
    if update_candidates:
        row["Last update"] = max(update_candidates).isoformat()

    # Reflect the newest of the incoming emails in the "latest" fields, but only
    # if it's not older than what's already recorded (don't let a late-arriving
    # stale email downgrade a further-along status).
    newest_entry, newest_date = max(dated, key=lambda pair: pair[1] or date.min)
    if is_new or existing_update is None or (newest_date and newest_date >= existing_update):
        _apply_latest(row, newest_entry)

    # Dedup ledger + count.
    ids = _msg_ids(row) | {e["message_id"] for e in entries if e.get("message_id")}
    row[_MSGIDS] = ",".join(sorted(ids))
    row["# of emails"] = str(len(ids))


def _new_row(company: str, role: str) -> dict:
    row = {h: "" for h in HEADERS}
    row["Company"] = company
    row["Role"] = role
    return row


# ---- entry point ---------------------------------------------------------

def sync_applications(entries: list[dict]) -> str:
    """Upsert job-related email entries into the tracker; return a status note.

    Each entry is a dict with: category, company, role, summary, date, raw_date,
    sender, subject, message_id. Raises SheetNotConfigured if GOOGLE_SHEET_ID is
    unset; other Google API errors propagate to the caller.
    """
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise SheetNotConfigured()
    if not entries:
        return ""

    logger.info("Syncing %d email(s) to the application tracker...", len(entries))
    service = build_service("sheets", "v4")
    _ensure_tab(service, sheet_id)
    rows, header_present = _read_rows(service, sheet_id)

    # Index existing rows by application key (first occurrence wins).
    index_by_key: dict[str, int] = {}
    for i, row in enumerate(rows):
        index_by_key.setdefault(_app_key(row.get("Company", ""), row.get("Role", "")), i)

    # Group incoming entries by the same key.
    groups: dict[str, list[dict]] = {}
    key_display: dict[str, tuple[str, str]] = {}
    for entry in entries:
        company = _effective_company(entry.get("company", ""), entry.get("sender", ""))
        role = (entry.get("role", "") or "").strip()
        key = _app_key(company, role)
        groups.setdefault(key, []).append(entry)
        key_display.setdefault(key, (company, role))

    added = updated = 0
    for key, group in groups.items():
        if key in index_by_key:
            row = rows[index_by_key[key]]
            seen = _msg_ids(row)
            fresh = [e for e in group if e.get("message_id") not in seen]
            if not fresh:
                continue  # every email already recorded; nothing to do
            _fold(row, fresh, is_new=False)
            updated += 1
        else:
            company, role = key_display[key]
            row = _new_row(company, role)
            _fold(row, group, is_new=True)
            rows.append(row)
            index_by_key[key] = len(rows) - 1
            added += 1

    if added or updated or not header_present:
        _write_table(service, sheet_id, rows)
    logger.info("Tracker sync done: %d added, %d updated", added, updated)

    if not added and not updated:
        return "_📊 Tracker: no new application updates._"
    parts = []
    if added:
        parts.append(f"added {added}")
    if updated:
        parts.append(f"updated {updated}")
    return f"_📊 Tracker: {', '.join(parts)} in your Google Sheet._"
