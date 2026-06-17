#!/usr/bin/env python3
"""
Glassdoor Remote Job Search URL Generator
-------------------------------------------
Builds a Glassdoor search URL for a given job title, filtered to
remote jobs posted within a chosen time window (default: 24 hours).

Usage:
    python glassdoor_url.py "ai application developer"
    python glassdoor_url.py "machine learning engineer" --days 7
    python glassdoor_url.py "backend developer" --days 30 --location-id 11047

Notes:
    - location_id 11047 is Glassdoor's internal ID for "Remote".
      If you ever want a real city/state instead of Remote, you'd
      need to grab that location's numeric ID from a fresh Glassdoor
      search URL (the IS##### segment) and pass it via --location-id.
    - fromAge accepts 1, 3, 7, 14, or 30 (days since posting).
"""

import re

from settings import GLASSDOOR_LOCATION_SLUG,GLASSDOOR_REMOTE_LOCATION_ID, VALID_FROM_AGE

def slugify(title: str) -> str:
    """Convert a job title into Glassdoor's hyphenated URL slug format."""
    title = title.strip().lower()
    title = re.sub(r"[^a-z0-9\s-]", "", title)   # strip punctuation
    title = re.sub(r"\s+", "-", title)           # spaces -> hyphens
    title = re.sub(r"-+", "-", title)             # collapse repeats
    return title.strip("-")


def build_glassdoor_url(
    job_title: str,
    days: int = 1,
    location_slug: str = GLASSDOOR_LOCATION_SLUG,
    location_id: str = GLASSDOOR_REMOTE_LOCATION_ID,
) -> str:
    if days not in VALID_FROM_AGE:
        raise ValueError(
            f"days must be one of {sorted(VALID_FROM_AGE)} (Glassdoor's supported windows)"
        )

    keyword_slug = slugify(job_title)
    loc_len = len(location_slug)
    kw_start = loc_len + 1          # +1 accounts for the hyphen joining location + keyword
    kw_end = kw_start + len(keyword_slug)

    url = (
        f"https://www.glassdoor.com/Job/{location_slug}-{keyword_slug}-jobs-"
        f"SRCH_IL.0,{loc_len}_IS{location_id}_KO{kw_start},{kw_end}.htm"
        f"?fromAge={days}"
    )
    return url