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
from urllib.parse import urlencode

from settings import (
    GLASSDOOR_LOCATION_SLUG,
    GLASSDOOR_REMOTE_LOCATION_ID,
    VALID_FROM_AGE,
    BUILTIN_ENTRY_LEVEL_PATH,
    BUILTIN_COUNTRY,
    DICE_EMPLOYMENT_TYPE,
    DICE_WORKPLACE_TYPE,
    DICE_POSTED_DATE,
    DICE_POSTED_DATE_FALLBACK,
    JOBICY_CATEGORY,
    JOBICY_JOB_TYPE,
    JOBICY_JOB_LEVELS,
)
from logger import get_logger

logger = get_logger(__name__)

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
        logger.error("Invalid days=%s; must be one of %s", days, sorted(VALID_FROM_AGE))
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
    logger.debug("Built Glassdoor URL: %s", url)
    return url

def build_indeed_url(
    job_title: str,
    days: int = 1,
    location_slug: str = GLASSDOOR_LOCATION_SLUG,
) -> str:
    if days not in VALID_FROM_AGE:
        logger.error("Invalid days=%s; must be one of %s", days, sorted(VALID_FROM_AGE))
        raise ValueError(
            f"days must be one of {sorted(VALID_FROM_AGE)} (Glassdoor's supported windows)"
        )

    keyword_slug = slugify(job_title)
    loc_len = len(location_slug)
    kw_start = loc_len + 1          # +1 accounts for the hyphen joining location + keyword
    kw_end = kw_start + len(keyword_slug)

    first_url = (
        f"https://www.indeed.com/jobs?q={keyword_slug}&l={location_slug}&fromage={days}"
    )
    
    remaining_url = ('https://www.indeed.com/jobs?q={QUERY}&l={LOCATION}&fromage={DAYS}&start={OFFSET}')

    logger.debug("Built Indeed URL: %s", first_url)
    return first_url, remaining_url, keyword_slug


def build_builtin_url(job_title: str, days: int = 1) -> str:
    """Build a Built In search URL for a job title.

    Filters to fully-remote, US, entry-level/junior roles posted within the
    last `days` days. Built In filters to junior at the source (the
    /entry-level/junior path), which fits the candidate and keeps senior roles
    out of the pipeline. Returns only the page-1 URL; pagination is handled by
    the scraper appending `&page=N` (see fc.scrape_page_builtin).
    """
    if days not in VALID_FROM_AGE:
        logger.error("Invalid days=%s; must be one of %s", days, sorted(VALID_FROM_AGE))
        raise ValueError(
            f"days must be one of {sorted(VALID_FROM_AGE)}"
        )

    # urlencode handles escaping (e.g. "software engineer" -> "software+engineer").
    query = urlencode(
        {
            "search": job_title.strip(),
            "daysSinceUpdated": days,
            "country": BUILTIN_COUNTRY,
            "allLocations": "true",
        }
    )

    url = f"https://builtin.com/{BUILTIN_ENTRY_LEVEL_PATH}?{query}"
    logger.debug("Built Built In URL: %s", url)
    return url


def build_dice_url(job_title: str, days: int = 1) -> str:
    """Build a Dice search URL for a job title.

    Filters to full-time, fully-remote roles posted within the last `days` days.
    Dice's postedDate filter only supports ONE/THREE/SEVEN-day windows, so days
    are mapped onto those tokens (14/30 fall back to SEVEN, Dice's widest).
    Returns only the page-1 URL; pagination is handled by the scraper appending
    `&page=N` (see fc.scrape_page_dice).
    """
    if days not in VALID_FROM_AGE:
        logger.error("Invalid days=%s; must be one of %s", days, sorted(VALID_FROM_AGE))
        raise ValueError(
            f"days must be one of {sorted(VALID_FROM_AGE)}"
        )

    posted_date = DICE_POSTED_DATE.get(days, DICE_POSTED_DATE_FALLBACK)

    # urlencode handles escaping (e.g. "software engineer" -> "software+engineer").
    # The filters.* keys carry dots, which urlencode leaves intact.
    query = urlencode(
        {
            "filters.postedDate": posted_date,
            "filters.employmentType": DICE_EMPLOYMENT_TYPE,
            "filters.workplaceTypes": DICE_WORKPLACE_TYPE,
            "q": job_title.strip(),
        }
    )

    url = f"https://www.dice.com/jobs?{query}"
    logger.debug("Built Dice URL: %s", url)
    return url


def build_jobicy_url(job_title: str, days: int = 1) -> str:
    """Build a Jobicy search URL for a job title.

    Scoped to the engineering category and filtered to full-time,
    entry-level/junior roles (remote-only board), posted within the last `days`
    days. Jobicy's filter_by_day takes the day count directly, so 1/3/7/14/30
    map straight through. Pagination is AJAX "load more" with no page-N URL, so
    only this (page-1) URL is scraped (see run_job_search).
    """
    if days not in VALID_FROM_AGE:
        logger.error("Invalid days=%s; must be one of %s", days, sorted(VALID_FROM_AGE))
        raise ValueError(
            f"days must be one of {sorted(VALID_FROM_AGE)}"
        )

    # doseq=True expands the job-level list into repeated filter_job_level[]
    # params; urlencode escapes the "[]" keys to %5B%5D and spaces to "+".
    query = urlencode(
        {
            "search_keywords": job_title.strip(),
            "filter_job_type[]": JOBICY_JOB_TYPE,
            "filter_job_level[]": JOBICY_JOB_LEVELS,
            "filter_by_day_check": "on",
            "filter_by_day": days,
        },
        doseq=True,
    )

    url = f"https://jobicy.com/categories/{JOBICY_CATEGORY}?{query}"
    logger.debug("Built Jobicy URL: %s", url)
    return url