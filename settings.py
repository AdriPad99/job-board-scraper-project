GLASSDOOR_LOCATION_SLUG = "remote"
GLASSDOOR_REMOTE_LOCATION_ID = "11047"
VALID_FROM_AGE = {1, 3, 7, 14, 30}

# Built In. The /entry-level/junior path filters to junior roles at the source
# (fully-remote, US), which fits the candidate and keeps senior listings out of
# the scrape/score pipeline entirely.
BUILTIN_ENTRY_LEVEL_PATH = "jobs/remote/entry-level/junior"
BUILTIN_COUNTRY = "USA"