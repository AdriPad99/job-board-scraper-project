import json

from utils import generate_url_from_arg, scrape_job_details, find_applicable_jobs
from fc import scrape_page
from claude import call_claude
from models import JobList
from prompts import JOB_URL_PROMPT, JOB_URL_SYSTEM
from logger import setup_logging, get_logger

logger = get_logger(__name__)

chat_history = []

jobs = []

def main():

    setup_logging()

    url = generate_url_from_arg()
    logger.info("Generated search URL: %s", url)

    logger.info("Scraping page...")
    page = scrape_page(url, ['markdown'])

    url_prompt = JOB_URL_PROMPT.format(scrape=page.markdown)

    logger.info("Extracting jobs with Claude...")
    response = call_claude(prompt=url_prompt, history=chat_history, model=JobList, system=JOB_URL_SYSTEM)
    logger.info("Extracted %d job(s)", len(response.jobs))

    # print(json.dumps(response.model_dump(), indent=2))
    
    details = scrape_job_details(response)

    # print(json.dumps(details, indent=2))

    find_applicable_jobs(curr_jobs_list=jobs,available_jobs=details)

    logger.info("Done. %d job(s) to apply to", len(jobs))

    print(jobs)

if __name__ == "__main__":
    main()
