import argparse
import os
import re
from concurrent.futures import ThreadPoolExecutor

from settings import GLASSDOOR_LOCATION_SLUG, GLASSDOOR_REMOTE_LOCATION_ID
from url_converter import build_glassdoor_url, build_indeed_url
from models import JobDetails, JobList, AppliableJob
from claude import call_claude, call_claude_with_resume, encode_pdf, EXTRACTION_MODEL
from prompts import (
    JOB_DESCRIPTION_PROMPT,
    JOB_DESCRIPTION_SYSTEM,
    JOB_QUALIFICATION_PROMPT,
    JOB_QUALIFICATION_SYSTEM,
)
from fc import scrape_page
from logger import get_logger

logger = get_logger(__name__)

# The per-job detail scrape and resume-qualification loops are network/Claude
# bound, so they run in a bounded thread pool rather than sequentially. Keep the
# pool small so we don't trip Firecrawl/Anthropic rate limits; override with the
# JOB_SCRAPER_WORKERS env var.
MAX_WORKERS = max(1, int(os.getenv("JOB_SCRAPER_WORKERS", "5")))


def get_search_urls(
    job_title: str,
    days: int = 1,
    location_slug: str = GLASSDOOR_LOCATION_SLUG,
    location_id: str = GLASSDOOR_REMOTE_LOCATION_ID,
):
    """Build the Glassdoor + Indeed search URLs for a job title.

    Pure function shared by the CLI (generate_url_from_arg) and the Discord bot;
    it takes plain parameters instead of reading argv so callers can supply them
    however they like (argparse, slash-command options, etc.).
    """
    logger.debug(
        "Building URL (title=%r, days=%s, location_slug=%s, location_id=%s)",
        job_title, days, location_slug, location_id,
    )
    glassdoor_url = build_glassdoor_url(
        job_title=job_title,
        days=days,
        location_slug=location_slug,
        location_id=location_id,
    )

    first_url, remaining_url, usr_query = build_indeed_url(
        job_title=job_title,
        days=days,
        location_slug=location_slug,
    )

    return glassdoor_url, first_url, remaining_url, usr_query


def generate_url_from_arg() -> str:

    parser = argparse.ArgumentParser(description="Generate a Glassdoor remote job search URL.")
    parser.add_argument("job_title", help='Job title to search for, e.g. "ai application developer"')
    parser.add_argument(
        "--days", type=int, default=1,
        help="Posting age filter in days: 1, 3, 7, 14, or 30 (default: 1 = past 24 hours)"
    )
    parser.add_argument(
        "--location-id", default=GLASSDOOR_REMOTE_LOCATION_ID,
        help="Glassdoor numeric location ID (default: 11047 = Remote)"
    )
    parser.add_argument(
        "--location-slug", default=GLASSDOOR_LOCATION_SLUG,
        help='Location text slug used in the URL (default: "remote")'
    )
    args = parser.parse_args()

    return get_search_urls(
        job_title=args.job_title,
        days=args.days,
        location_slug=args.location_slug,
        location_id=args.location_id,
    )

def scrape_job_details(job_urls: JobList) -> list[JobDetails]:

    total = len(job_urls.jobs)
    logger.info("Scraping details for %d job(s)", total)

    def _scrape_one(indexed):
        index, job = indexed

        # chat_history is local per job so concurrent workers never share it.
        chat_history = []
        details = {"job_url": job.job_url}

        logger.info("[%d/%d] Scraping job: %s", index, total, job.job_url)

        try:
            url_scrape = scrape_page(url=job.job_url, formats=['markdown'])

            description_prompt = JOB_DESCRIPTION_PROMPT.format(scrape=url_scrape.markdown)

            response = call_claude(
                prompt=description_prompt,
                history=chat_history,
                model=JobDetails,
                system=JOB_DESCRIPTION_SYSTEM,
                model_id=EXTRACTION_MODEL,
            )
        except Exception:
            logger.exception("[%d/%d] Failed to scrape job: %s", index, total, job.job_url)
            return None

        details["job_title"] = response.job_title
        details["description"] = response.description
        details["salary"] = response.salary

        logger.debug("[%d/%d] Parsed job: %s", index, total, response.job_title)

        return details

    # executor.map preserves input order; failed jobs come back as None.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = executor.map(_scrape_one, enumerate(job_urls.jobs, start=1))

    job_scrapes = [details for details in results if details is not None]

    logger.info("Successfully scraped %d/%d job(s)", len(job_scrapes), total)

    return job_scrapes

# Title keywords that signal a role above the candidate's (junior) level. Used
# to skip the qualification call entirely so we don't spend tokens — and re-send
# the resume PDF — on a posting we'd reject anyway. "I" is intentionally excluded
# (that's junior); II/III/IV cover the mid/senior numbered variants.
_ABOVE_LEVEL_PATTERN = re.compile(
    r"\b(senior|sr|staff|principal|lead|director|manager|architect"
    r"|intermediate|mid|ii|iii|iv)\b",
    re.IGNORECASE,
)


def is_above_level(job_title: str) -> bool:
    """True if the job title looks senior/mid-level (not a fit for a junior candidate)."""
    return bool(_ABOVE_LEVEL_PATTERN.search(job_title or ""))


# Captures the lower bound of a years-of-experience requirement, handling
# "5+ years", "5-7 years", "5 to 7 yrs", "5 years", etc. The first \d group is
# the lower bound, which is what gates junior eligibility.
_YEARS_PATTERN = re.compile(
    r"(\d{1,2})\s*(?:\+|\s*[-–]\s*\d{1,2}|\s*to\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)


def requires_excess_experience(description: str, min_years: int = 3) -> bool:
    """True only if every stated experience requirement is >= min_years.

    Conservative on purpose: a description mentioning any lower requirement
    (e.g. "2 years") is kept, so junior-friendly roles aren't dropped because a
    higher number appears elsewhere (e.g. "5 years preferred").
    """
    matches = [int(m.group(1)) for m in _YEARS_PATTERN.finditer(description or "")]
    return bool(matches) and min(matches) >= min_years


def find_applicable_jobs(curr_jobs_list: list[dict], available_jobs: list[JobDetails], resume_data):

    total = len(available_jobs)
    logger.info("Evaluating %d job(s) against resume", total)

    def _evaluate_one(indexed):
        index, job_listing = indexed

        job_url = job_listing.get("job_url", "<unknown>")
        job_title = job_listing.get("job_title", "")

        # Cheap title-based pre-filter: skip senior/mid-level roles before the
        # (token-heavy, resume-attached) qualification call.
        if is_above_level(job_title):
            logger.info("[%d/%d] Skipping senior/mid-level role: %s", index, total, job_title)
            return None

        if requires_excess_experience(job_listing.get("description", "")):
            logger.info("[%d/%d] Skipping role requiring 5+ years: %s", index, total, job_title)
            return None

        logger.info("[%d/%d] Evaluating job: %s", index, total, job_url)

        # Only the title + description inform the apply/skip decision; dropping
        # job_url, salary, and dict punctuation trims input tokens per job.
        posting = (
            f"Title: {job_listing.get('job_title', '')}\n"
            f"Description: {job_listing.get('description', '')}"
        )
        qualifications_prompt = JOB_QUALIFICATION_PROMPT.format(posting=posting)

        try:
            result = call_claude_with_resume(
                resume_data=resume_data,
                prompt=qualifications_prompt,
                system=JOB_QUALIFICATION_SYSTEM,
                model=AppliableJob,
            )
        except Exception:
            logger.exception("[%d/%d] Failed to evaluate job: %s", index, total, job_url)
            return None

        if result.recommendation == 'APPLY' or result.recommendation == 'STRETCH':
            logger.info("[%d/%d] Match (%s): %s", index, total, result.recommendation, job_url)
            # Pair the scraped posting (url/title/salary/description) back with the
            # recommendation. AppliableJob itself only carries reasoning + verdict,
            # so returning it alone would strip the job's identity — the downstream
            # markdown converter would then have no real title/URL/salary to render
            # and would hallucinate listings (reintroducing roles the filters above
            # already rejected). Merging keeps the output tied to the filtered set.
            return {
                **job_listing,
                "recommendation": result.recommendation,
                "reasoning": result.reasoning,
            }

        logger.debug("[%d/%d] No match: %s", index, total, job_url)
        return None

    # executor.map preserves input order; non-matches/failures come back as None.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = executor.map(_evaluate_one, enumerate(available_jobs, start=1))

    # Extend once on the calling thread rather than appending from workers, so we
    # don't rely on list.append being atomic under concurrency.
    curr_jobs_list.extend(result for result in results if result is not None)

    logger.info("Found %d applicable job(s) out of %d", len(curr_jobs_list), total)

    return

def get_resume_path():

    # tkinter is imported lazily (and only here) so headless deployments — e.g.
    # the Discord bot on a slim server image without python3-tk — can import this
    # module without pulling in Tk. Only the local CLI resume picker needs it.
    import tkinter as tk
    from tkinter import filedialog

    # open a file picker dialog so the user can select their resume/CV pdf
    root = tk.Tk()
    root.withdraw()  # hide the empty root window, only show the dialog

    pdf_path = filedialog.askopenfilename(
        title="Select a PDF file",
        filetypes=[("PDF files", "*.pdf")],
    )

    root.destroy()

    if not pdf_path:
        logger.warning("No PDF file selected")
        return

    logger.info("Selected PDF file: %s", pdf_path)

    # Encode the resume once; it's reused (and prompt-cached) across every job.
    resume_data = encode_pdf(pdf_path)
    
    return resume_data