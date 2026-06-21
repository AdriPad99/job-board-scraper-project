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

def scrape_page_indeed(url1: str, url2: str, usr_job: str):
    
    listings = []
    
    for i in list(range(1,4)):
        
        if i == 1:

            results = scrape_page(url=url1, formats=['markdown'])
            content = results.markdown or ""
            listings.append(content)

            if len(content) < 500:

                break

        else:

            search_url = url2.format(QUERY=usr_job, LOCATION="remote", DAYS="1", OFFSET=f"{i * 10}")

            results = scrape_page(url=search_url, formats=['markdown'])
            content = results.markdown or ""
            listings.append(content)

            if len(content) < 500:

                break
            
    return listings