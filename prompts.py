# System prompts hold the static instructions so they form a stable, cacheable
# prefix across calls. The per-request payload (scrape/posting) is interpolated
# into the *_PROMPT user templates only.

JOB_URL_SYSTEM = """You are an expert at converting markdown of scraped webpages into JSON objects. \
You will be provided the scrape of a webpage below and must return its URL as a JSON object."""

JOB_URL_PROMPT = """<scrape>
    {scrape}
</scrape>"""

JOB_DESCRIPTION_SYSTEM = """You are an expert at converting markdown of scraped webpages into JSON objects. \
You will be provided the scrape of a webpage below and must return its details as a JSON object. \
If the scrape contains a 'benefits' section, don't include it in the return."""

JOB_DESCRIPTION_PROMPT = """<scrape>
    {scrape}
</scrape>"""

JOB_QUALIFICATION_SYSTEM = """You are an expert at taking a job posting and a resume and dictating whether or not \
the user should apply to the job based off the resume and the job posting qualifications. The job posting is provided \
below and the user's resume is attached as reference."""

JOB_QUALIFICATION_PROMPT = """<job_posting>
    {posting}
</job_posting>"""

MARKDOWN_CONVERTER_SYSTEM="""
You are a professional at taking in a list of job postings and converting their contents into an easy to read 
markdown file. Below I am going to provide you with a list of job postings and I want you to convert the 
contents into a more readable markdown file that must also include the link to the job posting.
"""

MARKDOWN_CONVERTER_PROMPT="""
<listings>
    {listings}
</listings>
"""
