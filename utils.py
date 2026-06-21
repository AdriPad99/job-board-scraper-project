import argparse
import tkinter as tk
from tkinter import filedialog

from settings import GLASSDOOR_LOCATION_SLUG, GLASSDOOR_REMOTE_LOCATION_ID
from url_converter import build_glassdoor_url, build_indeed_url
from models import JobDetails, JobList, AppliableJob
from claude import call_claude, call_claude_with_resume, encode_pdf
from prompts import (
    JOB_DESCRIPTION_PROMPT,
    JOB_DESCRIPTION_SYSTEM,
    JOB_QUALIFICATION_PROMPT,
    JOB_QUALIFICATION_SYSTEM,
)
from fc import scrape_page
from logger import get_logger

logger = get_logger(__name__)

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

    logger.debug(
        "Building URL (title=%r, days=%s, location_slug=%s, location_id=%s)",
        args.job_title, args.days, args.location_slug, args.location_id,
    )
    glassdoor_url = build_glassdoor_url(
        job_title=args.job_title,
        days=args.days,
        location_slug=args.location_slug,
        location_id=args.location_id,
    )
    
    first_url, remaining_url, usr_query = build_indeed_url(
        job_title=args.job_title,
        days=args.days,
        location_slug=args.location_slug
    )

    return glassdoor_url, first_url, remaining_url, usr_query

def scrape_job_details(job_urls: JobList) -> list[JobDetails]:

    job_scrapes = []

    total = len(job_urls.jobs)
    logger.info("Scraping details for %d job(s)", total)

    for index, job in enumerate(job_urls.jobs, start=1):

        details = {}

        chat_history = []

        details["job_url"] = job.job_url

        logger.info("[%d/%d] Scraping job: %s", index, total, job.job_url)

        try:
            url_scrape = scrape_page(url=job.job_url, formats=['markdown'])

            description_prompt = JOB_DESCRIPTION_PROMPT.format(scrape=url_scrape.markdown)

            response = call_claude(
                prompt=description_prompt,
                history=chat_history,
                model=JobDetails,
                system=JOB_DESCRIPTION_SYSTEM,
            )
        except Exception:
            logger.exception("[%d/%d] Failed to scrape job: %s", index, total, job.job_url)
            continue

        details["job_title"] = response.job_title
        details["description"] = response.description
        details["salary"] = response.salary

        logger.debug("[%d/%d] Parsed job: %s", index, total, response.job_title)

        job_scrapes.append(details)

    logger.info("Successfully scraped %d/%d job(s)", len(job_scrapes), total)

    return job_scrapes

def find_applicable_jobs(curr_jobs_list: list[AppliableJob], available_jobs: list[JobDetails], resume_data):

    total = len(available_jobs)
    logger.info("Evaluating %d job(s) against resume", total)

    for index, job_listing in enumerate(available_jobs, start=1):

        job_url = job_listing.get("job_url", "<unknown>")
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
            continue

        if result.user_should_apply_to_job:

            logger.info("[%d/%d] Match: %s", index, total, job_url)
            curr_jobs_list.append(result)
        else:
            logger.debug("[%d/%d] No match: %s", index, total, job_url)

    logger.info("Found %d applicable job(s) out of %d", len(curr_jobs_list), total)

    return

def get_resume_path():
    
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