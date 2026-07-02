import argparse

from pipeline import run_job_search
from utils import get_resume_path
from logger import setup_logging, get_logger

logger = get_logger(__name__)


def main():
    """Local CLI entry point. Picks a resume via a file dialog, runs the search,
    and prints the markdown. The Discord bot (bot.py) is the primary front-end;
    this stays for local testing without Discord.
    """
    setup_logging()

    parser = argparse.ArgumentParser(description="Scrape job boards and score postings against a resume.")
    parser.add_argument("job_title", help='Job title to search for, e.g. "ai application developer"')
    parser.add_argument(
        "--days", type=int, default=1,
        help="Posting age filter in days: 1, 3, 7, 14, or 30 (default: 1 = past 24 hours)",
    )
    args = parser.parse_args()

    # get path to resume to be scanned (returns the base64-encoded PDF)
    resume = get_resume_path()
    if resume is None:
        logger.error("No resume selected; aborting.")
        return

    markdown = run_job_search(job_title=args.job_title, resume_data=resume, days=args.days)

    print(markdown)


if __name__ == "__main__":
    main()
