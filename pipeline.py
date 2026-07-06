"""Core job-search pipeline, decoupled from any front-end (CLI or Discord bot).

`run_job_search` takes plain parameters (job title, age filter, already-encoded
resume) and returns the final markdown as a string instead of printing it, so a
caller can print it, upload it, or send it to a channel.
"""

import re
from dataclasses import dataclass, field

from utils import get_search_urls, scrape_job_details, find_applicable_jobs
from fc import scrape_page, scrape_page_indeed
from claude import call_claude, call_claude_with_resume
from latex import compile_latex, LatexNotInstalled, cover_letter_to_latex
from models import JobList, ApplicationDraft, TailoredResume, LatexFix
from prompts import (
    JOB_URL_PROMPT,
    JOB_URL_SYSTEM,
    APPLICATION_DRAFT_PROMPT,
    APPLICATION_DRAFT_SYSTEM,
    RESUME_TAILOR_PROMPT,
    RESUME_TAILOR_SYSTEM,
    LATEX_FIX_PROMPT,
    LATEX_FIX_SYSTEM,
)
from logger import get_logger

logger = get_logger(__name__)


# Matches an em dash — the unicode characters (— ―) or the LaTeX "---" ligature —
# plus any horizontal whitespace hugging it, so it can be swapped for a comma.
_EM_DASH_RE = re.compile(r"[ \t]*(?:—|―|---)[ \t]*")


def _remove_em_dashes(text: str) -> str:
    """Replace em dashes with a comma so drafts don't carry the tell-tale AI em
    dash. A deterministic backstop to the prompt-level rule."""
    return _EM_DASH_RE.sub(", ", text)


# Inflation / overstatement terms a junior candidate shouldn't claim. The prompts
# tell the model to avoid these; this scanner is a NON-destructive backstop that
# flags any that slip through (rewriting them would break the prose), so they can
# be reviewed before sending.
_BANNED_TERM_RE = {
    label: re.compile(pattern, re.IGNORECASE)
    for label, pattern in {
        "production": r"\bproduction\b",
        "enterprise": r"\benterprise\b",
        "at scale": r"\bat scale\b",
        "many": r"\bmany\b",
        "numerous": r"\bnumerous\b",
        "extensive": r"\bextensiv(?:e|ely)\b",
        "seasoned": r"\bseasoned\b",
        "years of experience": r"\byears of experience\b",
    }.items()
}


def find_banned_terms(text: str) -> list[str]:
    """Return the distinct overstatement terms present in text (for a warning)."""
    return [label for label, rx in _BANNED_TERM_RE.items() if rx.search(text)]


def run_job_search(job_title: str, resume_data: str, days: int = 1) -> str:
    """Run the full scrape → extract → score → format pipeline for one search.

    Args:
        job_title: The role to search for, e.g. "ai application developer".
        resume_data: Base64-encoded resume PDF (see claude.encode_pdf_bytes).
        days: Posting-age filter in days (1, 3, 7, 14, or 30).

    Returns:
        Markdown summary of the applicable job postings.
    """
    # Per-invocation state. These were module-level globals in the old CLI, which
    # was fine for a one-shot process but would leak history/results across runs
    # in a long-lived bot — so they live here as locals.
    chat_history: list[dict] = []
    jobs: list = []

    # generate all applicable URL's
    glassdoor, indeed_url_one, indeed_url_remaining, usr_query = get_search_urls(
        job_title=job_title, days=days
    )

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

    # Scrapes all glassdoor and indeed jobs and compares each to a master resume
    # that then gets sent to AI to compare and choose which jobs are the best fit.
    find_applicable_jobs(curr_jobs_list=jobs, available_jobs=glassdoor_details, resume_data=resume_data)
    find_applicable_jobs(curr_jobs_list=jobs, available_jobs=indeed_details, resume_data=resume_data)

    logger.info("Done. %d job(s) to apply to", len(jobs))

    if not jobs:
        return f"No applicable job postings found for **{job_title}** (past {days} day(s))."

    return _format_jobs_markdown(jobs, job_title=job_title, days=days)


def _format_jobs_markdown(jobs: list[dict], job_title: str, days: int) -> str:
    """Render the already-filtered job list as markdown, deterministically.

    `jobs` has already been narrowed to the candidate's level by
    find_applicable_jobs, so this stage only presents it. Doing it in Python
    (rather than an LLM "prettyizer") means nothing can hallucinate extra
    listings, an "Application Tips" section, or senior roles back into the
    output — you get exactly the postings that passed the filters, and only
    the fields the pipeline actually scraped.
    """
    lines = [
        f"# Job matches for {job_title}",
        "",
        f"*{len(jobs)} role(s) matching your level, posted in the past {days} day(s).*",
    ]

    for index, job in enumerate(jobs, start=1):
        title = (job.get("job_title") or "Untitled role").strip()
        recommendation = job.get("recommendation")
        salary = job.get("salary")
        job_url = job.get("job_url")
        reasoning = (job.get("reasoning") or "").strip()
        description = (job.get("description") or "").strip()

        lines += ["", "---", "", f"## {index}. {title}", ""]
        if recommendation:
            lines.append(f"**Recommendation:** {recommendation}  ")
        # salary is an Optional[int] from the scrape; show it only when present.
        if salary:
            lines.append(f"**Salary:** ${salary:,}  ")
        if job_url:
            lines.append(f"**🔗 [Apply here]({job_url})**")
        if reasoning:
            lines += ["", f"**Why it fits:** {reasoning}"]
        if description:
            lines += ["", "### Description", "", description]

    return "\n".join(lines) + "\n"


@dataclass
class ApplicationDraftResult:
    """Outcome of draft_application: the full Markdown draft, an optional compiled
    cover-letter PDF, the company name (for filenames), a compile-status note, and
    any overstatement terms flagged in the cover letter for review."""
    markdown: str
    cover_letter_pdf: bytes | None
    company_name: str
    status: str
    warnings: list = field(default_factory=list)


def draft_application(job_url: str, resume_data: str, max_fix_attempts: int = 2) -> ApplicationDraftResult:
    """Scrape a single job posting and draft tailored application materials.

    Produces a Markdown draft (fit summary, cover letter, resume-tailoring
    suggestions, likely screening answers — all grounded in the attached resume)
    plus a best-effort compiled PDF of the cover letter. Draft-only; nothing is
    submitted anywhere.
    """
    logger.info("Scraping posting for application draft: %s", job_url)
    scrape = scrape_page(url=job_url, formats=['markdown'])
    posting = scrape.markdown or ""

    if len(posting) < 200:
        # A near-empty scrape usually means a login wall, a bot block, or a bad
        # link — the draft would be hollow, so bail with a clear message.
        logger.warning("Posting scrape looks empty (%d chars): %s", len(posting), job_url)
        return ApplicationDraftResult(
            markdown=(
                f"Couldn't read enough of the posting at {job_url} to draft an application "
                "(it may require a login, block scrapers, or the link may be wrong). "
                "Try a direct link to the job description."
            ),
            cover_letter_pdf=None,
            company_name="",
            status="",
        )

    prompt = APPLICATION_DRAFT_PROMPT.format(posting=posting)

    logger.info("Drafting application materials with Claude...")
    # Larger token budget than the terse APPLY/SKIP call: a cover letter plus
    # several screening answers can run long.
    draft = call_claude_with_resume(
        resume_data=resume_data,
        prompt=prompt,
        system=APPLICATION_DRAFT_SYSTEM,
        model=ApplicationDraft,
        max_tokens=8000,
    )
    logger.info("Application draft complete for %s", job_url)

    # Strip em dashes (backstop to the prompt rule) before rendering anywhere.
    cover_letter = _remove_em_dashes(draft.cover_letter)

    # Typeset the cover letter into a PDF (deterministic template + escaping, with
    # the compile/auto-fix loop as a safety net).
    logger.info("Compiling cover letter to PDF...")
    cover_tex = cover_letter_to_latex(cover_letter)
    cover_pdf, _final_source, status = _compile_with_retries(cover_tex, max_fix_attempts)

    return ApplicationDraftResult(
        markdown=_remove_em_dashes(_format_application_draft(draft, job_url)),
        cover_letter_pdf=cover_pdf,
        company_name=draft.company_name,
        status=status,
        warnings=find_banned_terms(cover_letter),
    )


def _format_application_draft(draft: ApplicationDraft, job_url: str) -> str:
    """Render an ApplicationDraft as copy-paste-friendly Markdown."""
    lines = [
        "# Application Draft",
        "",
        f"**Posting:** {job_url}",
        "",
        "## Fit summary",
        draft.fit_summary,
        "",
        "## Cover letter",
        draft.cover_letter,
        "",
        "## Resume tailoring suggestions",
    ]
    lines.extend(f"- {suggestion}" for suggestion in draft.resume_suggestions)
    lines.append("")
    lines.append("## Likely screening questions")
    for qa in draft.screening_answers:
        lines.extend(["", f"**Q: {qa.question}**", "", qa.answer])
    lines.extend([
        "",
        "---",
        "*Draft only — review, edit, and submit it yourself. Nothing was submitted on your behalf.*",
    ])
    return "\n".join(lines)


@dataclass
class TailoredResumeResult:
    """Outcome of tailor_resume: the final .tex, an optional compiled PDF, the
    change summary, and a human-readable note about the compilation result."""
    latex_source: str
    pdf_bytes: bytes | None
    change_summary: list[str]
    status: str
    company_name: str = ""
    warnings: list = field(default_factory=list)


def tailor_resume(job_url: str, latex_source: str, max_fix_attempts: int = 2) -> TailoredResumeResult:
    """Rewrite a LaTeX resume to target a posting and compile it to PDF.

    Claude tailors the resume content (grounded strictly in the original — no
    fabrication), then we compile it with pdflatex. On a compile failure the
    error is fed back to Claude to repair the source, up to max_fix_attempts.
    The .tex is always returned; the PDF is best-effort.
    """
    logger.info("Scraping posting to tailor resume: %s", job_url)
    scrape = scrape_page(url=job_url, formats=['markdown'])
    posting = scrape.markdown or ""

    if len(posting) < 200:
        logger.warning("Posting scrape looks empty (%d chars): %s", len(posting), job_url)
        return TailoredResumeResult(
            latex_source=latex_source,
            pdf_bytes=None,
            change_summary=[],
            status=(
                f"Couldn't read enough of the posting at {job_url} to tailor the resume "
                "(it may require a login, block scrapers, or the link may be wrong). "
                "Returned your original .tex unchanged."
            ),
        )

    logger.info("Tailoring resume with Claude...")
    tailored = call_claude(
        prompt=RESUME_TAILOR_PROMPT.format(posting=posting, latex=latex_source),
        history=[],
        model=TailoredResume,
        system=RESUME_TAILOR_SYSTEM,
    )
    logger.info("Resume tailored; %d change(s) noted", len(tailored.change_summary))

    # Strip em dashes (including the LaTeX "---" ligature) before compiling, so
    # both the returned .tex and the PDF are free of them.
    source = _remove_em_dashes(tailored.latex_source)
    pdf_bytes, final_source, status = _compile_with_retries(source, max_fix_attempts)

    return TailoredResumeResult(
        latex_source=_remove_em_dashes(final_source),
        pdf_bytes=pdf_bytes,
        change_summary=tailored.change_summary,
        status=status,
        company_name=tailored.company_name,
        warnings=find_banned_terms(source),
    )


def _compile_with_retries(source: str, max_fix_attempts: int):
    """Compile LaTeX, feeding any error back to Claude to fix and retrying.

    Returns (pdf_bytes_or_None, final_source, status_message).
    """
    try:
        pdf_bytes, error = compile_latex(source)
    except LatexNotInstalled:
        logger.warning("pdflatex not installed; skipping PDF compilation")
        return None, source, (
            "LaTeX (pdflatex) isn't installed on the host, so no PDF was produced."
        )

    attempt = 0
    while pdf_bytes is None and attempt < max_fix_attempts:
        attempt += 1
        logger.warning(
            "LaTeX compile failed (attempt %d/%d); asking Claude to fix", attempt, max_fix_attempts
        )
        fix = call_claude(
            prompt=LATEX_FIX_PROMPT.format(error=error, latex=source),
            history=[],
            model=LatexFix,
            system=LATEX_FIX_SYSTEM,
        )
        source = fix.latex_source
        pdf_bytes, error = compile_latex(source)

    if pdf_bytes is None:
        logger.error("LaTeX compile failed after %d fix attempt(s)", max_fix_attempts)
        return None, source, (
            f"Couldn't compile to PDF after {max_fix_attempts} auto-fix "
            f"attempt(s); last error:\n{error[-400:]}"
        )

    if attempt:
        logger.info("LaTeX compiled after %d auto-fix attempt(s)", attempt)
        return pdf_bytes, source, f"Compiled to PDF after {attempt} auto-fix attempt(s)."

    logger.info("LaTeX compiled on first attempt")
    return pdf_bytes, source, "Compiled to PDF successfully."
