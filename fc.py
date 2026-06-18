import os

from dotenv import load_dotenv
from firecrawl import FirecrawlApp
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