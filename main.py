import json
import argparse
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="firecrawl")

from fc import scrape_page
from settings import GLASSDOOR_LOCATION_SLUG, GLASSDOOR_REMOTE_LOCATION_ID
from url_converter import build_glassdoor_url

def main():
    
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

    url = build_glassdoor_url(
        job_title=args.job_title,
        days=args.days,
        location_slug=args.location_slug,
        location_id=args.location_id,
    )
    print(url)
    
    page = scrape_page(url, ['markdown'])
    print(json.dumps(page.markdown, indent=2))


if __name__ == "__main__":
    main()
