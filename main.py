import json

from utils import generate_url_from_arg, scrape_job_details, find_applicable_jobs, get_resume_path, build_indeed_url
from fc import scrape_page, scrape_page_indeed
from claude import call_claude
from models import JobList, Prettyizer
from prompts import JOB_URL_PROMPT, JOB_URL_SYSTEM, MARKDOWN_CONVERTER_PROMPT, MARKDOWN_CONVERTER_SYSTEM
from logger import setup_logging, get_logger

logger = get_logger(__name__)

chat_history = []

jobs = []

def main():

    setup_logging()
    
    # get path to resume to be scanned
    resume = get_resume_path()

    # generate all applicable URL's
    glassdoor, indeed_url_one, indeed_url_remaining, usr_query = generate_url_from_arg()
    
    logger.info("Generated search URL: %s", glassdoor)
    logger.info("Generated search URL: %s", indeed_url_one)

    # Scrape both glassdoor and indeed pages of their job postings
    logger.info("Scraping page...")
    glassdoor_page = scrape_page(glassdoor, ['markdown'])
    indeed_page = scrape_page_indeed(indeed_url_one, indeed_url_remaining, usr_query)

    glassdoor_prompt = JOB_URL_PROMPT.format(scrape=glassdoor_page.markdown)
    indeed_prompt = JOB_URL_PROMPT.format(scrape="\n\n".join(indeed_page))

    # scrape all URL links from the glassdoor and indeed scrapes
    logger.info("Extracting jobs with Claude...")
    glassdoor_response = call_claude(prompt=glassdoor_prompt, history=chat_history, model=JobList, system=JOB_URL_SYSTEM)
    indeed_response = call_claude(prompt=indeed_prompt, history=chat_history, model=JobList, system=JOB_URL_SYSTEM)
    logger.info("Extracted %d job(s) from glassdoor", len(glassdoor_response.jobs))
    logger.info("Extracted %d job(s) from indeed", len(indeed_response.jobs))

    # scrape URL links for details of each posting
    glassdoor_details = scrape_job_details(glassdoor_response)
    indeed_details = scrape_job_details(indeed_response)

    # Scrapes all glassdoor and inded jobs and compares each to a master resume 
    # that then gets sent to AI to compare and choose which jobs are the best fit to apply to.
    find_applicable_jobs(curr_jobs_list=jobs,available_jobs=glassdoor_details, resume_data=resume)
    find_applicable_jobs(curr_jobs_list=jobs,available_jobs=indeed_details, resume_data=resume)

    logger.info("Done. %d job(s) to apply to", len(jobs))
    
    markdown_prompt = MARKDOWN_CONVERTER_PROMPT.format(listings=jobs)
    
    markdown = call_claude(
        prompt=markdown_prompt, 
        history=chat_history, 
        model=Prettyizer, 
        system=MARKDOWN_CONVERTER_SYSTEM)

    print(markdown.formatted_content)

if __name__ == "__main__":
    main()
