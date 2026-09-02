import os
import time
from dotenv import load_dotenv
from firecrawl import FirecrawlApp
from firecrawl.v2.utils.error_handler import InternalServerError
from typing import Literal
from logger import get_logger

load_dotenv()
logger = get_logger(__name__)
app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))


def scrape_page(url: str, formats: list[Literal['markdown', 'html']]) -> dict:
    logger.debug("Scraping %s (formats=%s)", url, formats)
    data = app.scrape_url(
        url=url,
        formats=formats
    )
    logger.debug("Scrape complete for %s", url)
    return data


def scrape_page_with_retry(url: str, formats: list[Literal['markdown', 'html']], max_retries: int = 3) -> dict:
    """Wraps scrape_page with retries for transient Firecrawl proxy/tunnel errors."""
    for attempt in range(max_retries):
        try:
            return scrape_page(url=url, formats=formats)
        except InternalServerError as e:
            if "TUNNEL_CONNECTION" in str(e) and attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                logger.warning("Tunnel error scraping %s (attempt %d/%d), retrying in %ds", url, attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            raise


def scrape_page_indeed(url1: str, url2: str, usr_job: str):
    listings = []
    for i in list(range(1,4)):
        if i == 1:
            results = scrape_page_with_retry(url=url1, formats=['markdown'])
            content = results.markdown or ""
            listings.append(content)
            if len(content) < 500:
                break
        else:
            search_url = url2.format(QUERY=usr_job, LOCATION="remote", DAYS="1", OFFSET=f"{i * 10}")
            results = scrape_page_with_retry(url=search_url, formats=['markdown'])
            content = results.markdown or ""
            listings.append(content)
            if len(content) < 500:
                break
    return listings


def scrape_page_builtin(base_url: str, max_pages: int = 3):
    """Scrape Built In search-result pages, following pagination via `&page=N`.

    Mirrors scrape_page_indeed: walk up to max_pages, collecting each page's
    markdown, and stop early once a page returns almost nothing (i.e. we've run
    past the last page of listings). base_url is the page-1 URL from
    url_converter.build_builtin_url.
    """
    listings = []
    for page in range(1, max_pages + 1):
        # Page 1 is the base URL; subsequent pages just append &page=N.
        url = base_url if page == 1 else f"{base_url}&page={page}"
        results = scrape_page_with_retry(url=url, formats=['markdown'])
        content = results.markdown or ""
        listings.append(content)
        if len(content) < 500:
            break
    return listings


def scrape_page_dice(base_url: str, max_pages: int = 3):
    """Scrape Dice search-result pages, following pagination via `&page=N`.

    Mirrors scrape_page_builtin: walk up to max_pages, collecting each page's
    markdown, and stop early once a page returns almost nothing (i.e. we've run
    past the last page of listings). base_url is the page-1 URL from
    url_converter.build_dice_url.
    """
    listings = []
    for page in range(1, max_pages + 1):
        # Page 1 is the base URL; subsequent pages just append &page=N.
        url = base_url if page == 1 else f"{base_url}&page={page}"
        results = scrape_page_with_retry(url=url, formats=['markdown'])
        content = results.markdown or ""
        listings.append(content)
        if len(content) < 500:
            break
    return listings