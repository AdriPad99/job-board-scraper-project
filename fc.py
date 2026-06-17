import os

from dotenv import load_dotenv
from firecrawl import FirecrawlApp
from typing import Literal

load_dotenv()

app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))

def scrape_page(url: str, formats: list[Literal['markdown', 'html']]) -> dict:
    
    data = app.scrape_url(
        url=url,
        formats=formats
    )
    
    return data