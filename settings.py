GLASSDOOR_LOCATION_SLUG = "remote"
GLASSDOOR_REMOTE_LOCATION_ID = "11047"
VALID_FROM_AGE = {1, 3, 7, 14, 30}

# Built In. The /entry-level/junior path filters to junior roles at the source
# (fully-remote, US), which fits the candidate and keeps senior listings out of
# the scrape/score pipeline entirely.
BUILTIN_ENTRY_LEVEL_PATH = "jobs/remote/entry-level/junior"
BUILTIN_COUNTRY = "USA"

# Dice. Filtered to full-time, fully-remote roles at the source to match the
# rest of the pipeline's remote focus. Dice's postedDate filter only supports
# ONE/THREE/SEVEN-day windows, so days are mapped onto those tokens (14/30 fall
# back to SEVEN, Dice's widest window).
DICE_EMPLOYMENT_TYPE = "FULLTIME"
DICE_WORKPLACE_TYPE = "Remote"
DICE_POSTED_DATE = {1: "ONE", 3: "THREE", 7: "SEVEN"}
DICE_POSTED_DATE_FALLBACK = "SEVEN"

# Jobicy. A remote-only board (WP Job Manager under the hood). Scoped to the
# engineering category and filtered to full-time entry-level/junior roles at the
# source, matching the candidate and the rest of the pipeline's junior/remote
# focus. filter_by_day takes the posting-age day count directly (1/3/7/14/30).
# Pagination is AJAX "load more" (no page-N URL), so only page 1 is scraped.
JOBICY_CATEGORY = "engineering"
JOBICY_JOB_TYPE = "full-time"
JOBICY_JOB_LEVELS = ["entry-level", "junior"]