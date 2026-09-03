# Job Board Scraper

A Discord bot (with a local CLI fallback) that scrapes remote job postings from
**Glassdoor**, **Indeed**, **Built In**, **Dice**, and **Jobicy**, then uses **Claude** to extract each listing, pull
its details, and score it against your resume — returning a clean Markdown
summary of the roles actually worth applying to. It can also draft tailored
application materials for any posting link (`/apply`), rewrite your LaTeX
resume to target a specific posting, compiling it to PDF (`/tailor`), and triage
your inbox for application confirmations, rejections, offers, and interview
requests (`/checkemail`).

## How it works

1. Builds Glassdoor, Indeed, Built In, Dice, and Jobicy search URLs from a job title and posting-age filter.
2. Scrapes the results pages with [Firecrawl](https://firecrawl.dev).
3. Uses Claude to extract job links, scrape each posting's details, and evaluate
   fit against your resume (cheap regex pre-filters skip senior/high-experience
   roles before spending tokens).
4. Returns a Markdown report of the matching postings, with links.

## Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/) (for dependency management)
- API keys for **Anthropic** and **Firecrawl**
- A **Discord bot token** (for the bot; not needed for the CLI)
- **TeX Live** (`pdflatex`) — only for the `/tailor` command's PDF output. Without
  it, `/tailor` still returns the tailored `.tex`; it just skips compiling a PDF.
  Install with e.g. `sudo apt install texlive-latex-recommended texlive-latex-extra`.

## Setup

1. **Install dependencies:**

   ```bash
   uv sync
   ```

2. **Configure secrets.** Copy the example env file and fill in your keys:

   ```bash
   cp .env.example .env
   ```

   ```dotenv
   ANTHROPIC_API_KEY=sk-ant-...
   FIRECRAWL_API_KEY=fc-...
   DISCORD_BOT_TOKEN=...        # only required for the Discord bot
   DISCORD_GUILD_ID=...         # optional — instant command sync (see below)
   RESUME_OWNER=...             # optional — name/suffix in generated filenames
   CANDIDATE_PROFILE="..."      # optional — profile injected into prompts
   LOG_LEVEL=INFO               # optional (DEBUG, INFO, WARNING, ...)
   JOB_SCRAPER_WORKERS=5        # optional (see Performance below)
   ```

   `RESUME_OWNER` and `CANDIDATE_PROFILE` keep your name and background out of
   source (so the repo can be public). `CANDIDATE_PROFILE` is a short summary of
   your experience/skills/degree that steers scoring, cover letters, and resume
   tailoring; multi-line values must be wrapped in double quotes. See
   `.env.example` for the format.

## Usage — Discord bot

### One-time bot setup

1. Create an application at the
   [Discord Developer Portal](https://discord.com/developers/applications) and
   add a **Bot** to it.
2. Copy the bot token into `DISCORD_BOT_TOKEN` in your `.env`.
3. Invite the bot to your server using the **`applications.commands`** scope
   (and `bot`). No privileged intents are required.
4. *(Recommended for development)* Set **`DISCORD_GUILD_ID`** in `.env` to your
   server's ID. Slash commands then sync to that server **instantly** on startup.
   Without it, commands sync globally and new/changed ones can take **up to ~1
   hour** to appear. Get the ID via Discord → **Settings → Advanced → Developer
   Mode**, then right-click your server icon → **Copy Server ID**. Note that
   command changes only take effect after you **restart the bot** (it re-syncs on
   startup).

### Run it

```bash
uv run python bot.py
```

The bot registers four slash commands:

#### `/findjobs` — find & score postings

```
/findjobs job_title:<text> resume:<PDF attachment> [days:<1|3|7|14|30>]
```

- **`job_title`** — the role to search for, e.g. `ai application developer`.
- **`resume`** — attach your resume as a **PDF** (under 10 MB).
- **`days`** — optional posting-age filter; defaults to the past **1** day.

The search takes a while (it scrapes and evaluates every posting), so the bot
replies with the results as an uploaded **`job_matches.md`** file when it's done.

#### `/apply` — draft application materials

```
/apply job_url:<link> resume:<PDF attachment>
```

- **`job_url`** — link to the specific posting you want to apply to.
- **`resume`** — attach your resume as a **PDF** (under 10 MB).

Scrapes the posting and uses Claude (with your resume attached) to draft an
**`application_draft.md`** containing a fit summary, a tailored cover letter,
resume-tailoring suggestions, and drafted answers to likely screening questions.
It also attaches a typeset **`<company>_cover_letter_Adrian_P.pdf`** of the cover
letter (compiled with `pdflatex`; skipped with a note if TeX Live isn't installed).

> **Draft-only, on purpose.** The bot never submits anything — every draft is
> grounded strictly in your real resume (no fabricated experience) and is yours
> to review, edit, and submit. Automated submission is intentionally out of scope
> (it would require logins, trip CAPTCHAs/bot-detection, and violate job-board ToS).

#### `/tailor` — tailor your resume to a posting

```
/tailor job_url:<link> resume_tex:<.tex attachment>
```

- **`job_url`** — link to the posting to tailor toward.
- **`resume_tex`** — your resume's **LaTeX source** as a `.tex` file (under 1 MB).

Rewrites the resume's **content** to target the posting — reordering and
rewording bullets, aligning terminology, sharpening the summary — while keeping
your template/preamble intact and inventing nothing (same honesty guardrails as
`/apply`). It returns the edited **`tailored_resume.tex`** plus a summary of what
changed, and, if `pdflatex` is installed, a compiled **`tailored_resume.pdf`**.

#### `/checkemail` — triage job-application emails

```
/checkemail [days:<1-30>]
```

- **`days`** — optional look-back window; defaults to the past **7** days.

Scans your Gmail from the last `days` days, has Claude classify each message, and
replies with a summary grouped into **offers / acceptances**, **interview
requests**, **application confirmations**, and **rejections**. Job-board alert
digests ("new jobs matching your search") and other non-application mail are
filtered out. Gmail access is **read-only** (`gmail.readonly`) — the bot never
sends, deletes, or modifies mail.

**Application tracker (Google Sheets).** If you set `GOOGLE_SHEET_ID`, the same
run also upserts each job-related email into a Google Sheet — **one row per
application**, keyed on company + role. A new application appends a row; a
follow-up email (e.g. a rejection after a confirmation) updates that row's status
and dates in place. Emails are deduped by Gmail message ID, so re-running
`/checkemail` never double-counts. Columns:

> Company · Role · Status · Date applied · Last update · Latest email category ·
> Latest email summary · From · Subject · Gmail link · # of emails · Notes ·
> Message IDs *(internal dedup ledger — you can hide this column)*

The **Notes** column is yours to edit — the bot never overwrites it. The **Status**
cell is color-coded automatically (Offer 🟢 · Interviewing 🔵 · Confirmed 🟡 ·
Rejected 🔴) via conditional-formatting rules the bot installs once. If
`GOOGLE_SHEET_ID` is unset, `/checkemail` still returns the email summary and just
skips the sheet.

> **Private & owner-only.** Because it reads your personal inbox, `/checkemail`
> only runs for the Discord user in `DISCORD_OWNER_ID`, and its results are
> **ephemeral** (visible only to you, even in a shared server). Requires the
> Google OAuth setup below.

##### Checking your email — one-time Google setup

The deployed bot runs headless, so it authenticates to Google with a long-lived
refresh token you mint once on your own machine:

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project
   and **enable the Gmail API** and the **Google Sheets API**.
2. Configure an **OAuth consent screen** (External is fine; add your own Google
   account as a **test user** so you don't need app verification).
3. Create an **OAuth client ID** of type **Desktop app** and download its JSON as
   `credentials.json` (in the project root; it's git-ignored).
4. Run the helper and approve access in the browser window that opens (it requests
   read-only Gmail + read/write Sheets):
   ```
   uv run python get_gmail_token.py
   ```
5. Copy the three printed lines into your `.env` (and Railway variables):
   ```
   GMAIL_OAUTH_CLIENT_ID=...
   GMAIL_OAUTH_CLIENT_SECRET=...
   GMAIL_OAUTH_REFRESH_TOKEN=...
   ```
   *(Already set this up before the tracker existed? Re-run the helper so the new
   Sheets scope is granted, and replace the refresh token.)*
6. **For the tracker:** create (or pick) a Google Sheet owned by that same Google
   account, and set **`GOOGLE_SHEET_ID`** to the ID from its URL
   (`…/spreadsheets/d/`**`<ID>`**`/edit`). The bot creates an `Applications` tab and
   header row on first run. Leave it unset to skip the tracker.
7. Set **`DISCORD_OWNER_ID`** to your Discord user ID (enable Developer Mode, then
   right-click your name → **Copy User ID**). Without it, `/checkemail` refuses for
   everyone.

If compilation fails, the bot feeds the LaTeX error back to Claude to repair the
source and retries (up to twice). If it still can't compile, you get the `.tex`
and the compiler error so you can finish it in Overleaf or locally.

### Live progress

While a search runs, the bot posts a **status message that updates in real time**
with what it's doing — page scrapes, per-job progress (`[3/7] Scraping job…`),
and match counts — so you're not left staring at a spinner. The final results are
posted as a separate message that pings you.

Both `/findjobs` and `/apply` stream progress this way. The feed is built from
the app's logs, so it requires `LOG_LEVEL=INFO` (the default). If you raise the
level to `WARNING`, the progress feed will be empty (the results file is
unaffected). It also assumes one command at a time — running two simultaneously
will interleave their progress in each feed.

## Usage — local CLI

Runs the same pipeline without Discord. It opens a file dialog to pick your
resume PDF and prints the Markdown report to the terminal.

```bash
uv run python main.py "ai application developer"
uv run python main.py "machine learning engineer" --days 7
```

- **positional** `job_title` — the role to search for.
- **`--days`** — posting-age filter: `1`, `3`, `7`, `14`, or `30` (default `1`).

> Note: the CLI uses a `tkinter` file dialog to select the resume, so it needs a
> desktop environment (not a headless server).

## Performance

The per-posting detail scrape and resume evaluation run **concurrently** in a
bounded thread pool. Concurrency is capped by the `JOB_SCRAPER_WORKERS` env var
(default: **5**):

- **Higher** values finish faster but are more likely to hit Firecrawl or
  Anthropic rate limits.
- **Lower** values (e.g. `1` for fully sequential) are gentler on rate limits.

Note: parallel evaluation means the first few resume-qualification calls race
before Anthropic's prompt cache is warm, so the resume PDF is billed as a cache
write a few times rather than once — a small cost trade-off for the speedup.

## Deployment (Railway)

The repo ships a **`Dockerfile`** (uv + TeX Live so the PDF features work),
a **`railway.json`**, and a **`.dockerignore`**. The bot runs as a long-lived
**worker** — it connects out to Discord, so it needs **no exposed port** and no
public domain.

1. Push this repo to GitHub.
2. In [Railway](https://railway.com): **New Project → Deploy from GitHub repo**
   (or use the CLI: `railway init` then `railway up`). Railway detects the
   `Dockerfile` and builds it. The first build is slow — TeX Live is a large
   download.
3. Add the service **Variables**:
   - **Required:** `ANTHROPIC_API_KEY`, `FIRECRAWL_API_KEY`, `DISCORD_BOT_TOKEN`
   - **For `/checkemail`:** `DISCORD_OWNER_ID`, `GMAIL_OAUTH_CLIENT_ID`,
     `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN` (see "Checking your
     email" above). Omit them and every other command still works; only
     `/checkemail` is disabled.
   - **For the tracker (optional):** `GOOGLE_SHEET_ID` (and optionally
     `GOOGLE_SHEET_TAB`). Omit to run `/checkemail` without writing to a sheet.
   - **Optional:** `DISCORD_GUILD_ID` (instant command sync), `RESUME_OWNER`,
     `CANDIDATE_PROFILE`, `LOG_LEVEL`, `JOB_SCRAPER_WORKERS`

   These are read from the environment at startup — the bot **exits immediately**
   if a required key is missing (the Firecrawl/Anthropic clients are created on
   import), so set all three before the first deploy.
4. Railway redeploys on every push. The restart policy (`ON_FAILURE`, 10 retries)
   is set in `railway.json`.

> Railway may note that "no port was detected" — that's expected for a worker
> that doesn't serve HTTP; it keeps the process running regardless.

## Project layout

| File               | Responsibility                                              |
| ------------------ | ----------------------------------------------------------- |
| `bot.py`           | Discord bot / `/findjobs` `/apply` `/tailor` `/checkemail`  |
| `Dockerfile`       | Container image (uv + TeX Live) for deployment              |
| `railway.json`     | Railway build/deploy config                                 |
| `main.py`          | Local CLI entry point                                       |
| `pipeline.py`      | Job-search, application-draft & resume-tailor pipelines     |
| `latex.py`         | Compile LaTeX to PDF via system `pdflatex`                  |
| `utils.py`         | URL building, detail scraping, resume evaluation, filters   |
| `url_converter.py` | Glassdoor/Indeed/Built In/Dice/Jobicy search-URL builders   |
| `fc.py`            | Firecrawl scraping wrappers                                 |
| `claude.py`        | Claude / Instructor calls, PDF encoding                     |
| `models.py`        | Pydantic response models                                    |
| `prompts.py`       | System + user prompt templates                              |
| `settings.py`      | Location IDs and valid posting-age windows                  |
| `email_checker.py` | Gmail triage of job-application emails (`/checkemail`)      |
| `sheets_tracker.py`| Upserts applications into the Google Sheet tracker          |
| `gapi.py`          | Shared Google OAuth / timed API service clients            |
| `get_gmail_token.py` | One-time helper to mint the Google OAuth refresh token    |
| `logger.py`        | Logging setup                                               |
