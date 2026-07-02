"""Discord front-end for the job-board scraper.

Exposes three slash commands:
    /findjobs — scrape job boards and score postings against a resume PDF.
    /apply    — draft tailored application materials for a single posting link.
    /tailor   — rewrite a LaTeX resume to target a posting and compile it to PDF.

Each runs a long-running, fully synchronous pipeline off the event loop with
asyncio.to_thread, streaming progress into a live-updating message and uploading
the result file(s).

Setup:
    - Set DISCORD_BOT_TOKEN in .env (alongside ANTHROPIC_API_KEY / FIRECRAWL_API_KEY).
    - Invite the bot with the "applications.commands" scope so slash commands work.
    - Run: python bot.py
"""

import io
import os
import re
import asyncio
import logging
import contextlib
from dataclasses import dataclass, field

import discord
from discord import app_commands
from dotenv import load_dotenv

from pipeline import run_job_search, draft_application, tailor_resume
from claude import encode_pdf_bytes
from settings import VALID_FROM_AGE
from logger import setup_logging, get_logger

load_dotenv()

logger = get_logger(__name__)

# Loggers whose INFO records get mirrored into the Discord status message. These
# are the app's own module loggers (get_logger(__name__)); everything else
# (discord.py, httpx, ...) is excluded so the progress feed stays readable.
_APP_LOGGERS = {"pipeline", "utils", "fc", "claude", "url_converter"}

# How often (seconds) the live status message is edited. Keeps us well under
# Discord's per-channel edit rate limit while still feeling responsive.
_STATUS_REFRESH_SECONDS = 2.0


class DiscordStatusHandler(logging.Handler):
    """Mirror app log records into a single, live-updating Discord message.

    Records are emitted from worker threads, so emit() only buffers them (hopping
    onto the event loop thread via call_soon_threadsafe to mutate shared state).
    A separate asyncio task renders the buffer to the message on an interval, so
    we edit one message instead of spamming the channel.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, level: int = logging.INFO):
        super().__init__(level)
        self._loop = loop
        self.lines: list[str] = []
        self.dirty = False

    def emit(self, record: logging.LogRecord):
        if record.name not in _APP_LOGGERS:
            return
        try:
            msg = self.format(record)
        except Exception:
            return
        self._loop.call_soon_threadsafe(self._append, msg)

    def _append(self, msg: str):
        self.lines.append(msg)
        self.dirty = True

    def render(self) -> str:
        """Render the most recent lines as a code block under Discord's limit."""
        body = "\n".join(self.lines[-20:]) or "Starting…"
        # Leave headroom for the code-block fences within the 2000-char limit.
        if len(body) > 1800:
            body = "…\n" + body[-1800:]
        return f"```\n{body}\n```"

# The bot only needs slash commands; no privileged intents (message content,
# members, presence) are required, so the default intents are enough.
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Discord's max attachment size on the free tier is 25 MB; resumes are tiny, but
# reject anything unreasonable early to avoid pulling a large file into memory.
MAX_RESUME_BYTES = 10 * 1024 * 1024

# LaTeX source is plain text — a real resume is well under this.
MAX_TEX_BYTES = 1 * 1024 * 1024

# Owner suffix for tailored-resume / cover-letter filenames, e.g.
# <company>_resume_<RESUME_OWNER>. Read from the environment (keeps your name out
# of source) and sanitized to be filename-safe.
RESUME_OWNER = re.sub(r"[^A-Za-z0-9]+", "_", os.getenv("RESUME_OWNER", "candidate")).strip("_") or "candidate"


@dataclass
class CommandResult:
    """What a pipeline run delivers back to Discord: a message plus zero or more
    files as (filename, bytes) pairs."""
    message: str
    files: list = field(default_factory=list)

# Only these posting-age windows are valid (Glassdoor/Indeed constraint).
DAY_CHOICES = [
    app_commands.Choice(name=f"{d} day(s)", value=d)
    for d in sorted(VALID_FROM_AGE)
]


@client.event
async def on_ready():
    # Register (sync) the slash commands with Discord on startup.
    #
    # A guild-scoped sync registers commands *instantly* in that one server —
    # ideal for development, since a global sync can take up to ~1 hour for new
    # or changed commands to appear. Set DISCORD_GUILD_ID in .env to use it;
    # otherwise fall back to a global sync (visible in every server, eventually).
    guild_id = os.getenv("DISCORD_GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        logger.info(
            "Logged in as %s; synced %d command(s) to guild %s (instant)",
            client.user, len(synced), guild_id,
        )
    else:
        synced = await tree.sync()
        logger.info(
            "Logged in as %s; synced %d command(s) globally "
            "(new/changed commands can take up to ~1h to appear)",
            client.user, len(synced),
        )


@tree.command(name="findjobs", description="Scrape job boards and score postings against your resume.")
@app_commands.describe(
    job_title='Job title to search for, e.g. "ai application developer"',
    resume="Your resume as a PDF file",
    days="Only include postings from the last N days (default: 1)",
)
@app_commands.choices(days=DAY_CHOICES)
async def findjobs(
    interaction: discord.Interaction,
    job_title: str,
    resume: discord.Attachment,
    days: app_commands.Choice[int] | None = None,
):
    day_value = days.value if days is not None else 1

    # Validate before deferring (validation errors use the initial response).
    if await _reject_invalid_resume(interaction, resume):
        return

    # The pipeline takes well over Discord's 3-second ack window, so defer first.
    await interaction.response.defer(thinking=True)

    logger.info(
        "findjobs invoked by %s (title=%r, days=%s, resume=%s)",
        interaction.user, job_title, day_value, resume.filename,
    )

    resume_data = await _read_resume_data(interaction, resume)
    if resume_data is None:
        return

    def run():
        markdown = run_job_search(job_title=job_title, resume_data=resume_data, days=day_value)
        return CommandResult(
            message=f"Done — job matches for **{job_title}** (past {day_value} day(s)):",
            files=[("job_matches.md", markdown.encode("utf-8"))],
        )

    await _run_with_status(
        interaction,
        run,
        status_title=f"🔎 Searching for **{job_title}** (past {day_value} day(s))…",
        error_text="Something went wrong while searching for jobs. Check the bot logs for details.",
    )


@tree.command(name="apply", description="Draft tailored application materials for a job posting link.")
@app_commands.describe(
    job_url="Link to the job posting you want to apply to",
    resume="Your resume as a PDF file",
)
async def apply(
    interaction: discord.Interaction,
    job_url: str,
    resume: discord.Attachment,
):
    if await _reject_invalid_resume(interaction, resume):
        return

    # Basic URL sanity check before deferring.
    if not re.match(r"https?://\S+", job_url.strip(), re.IGNORECASE):
        await interaction.response.send_message(
            "Please provide a valid job posting link starting with `http(s)://`.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    logger.info(
        "apply invoked by %s (url=%s, resume=%s)",
        interaction.user, job_url, resume.filename,
    )

    resume_data = await _read_resume_data(interaction, resume)
    if resume_data is None:
        return

    url = job_url.strip()

    def run():
        result = draft_application(job_url=url, resume_data=resume_data)
        files = [("application_draft.md", result.markdown.encode("utf-8"))]
        message = f"Here's your application draft for <{url}> — review, edit, and submit it yourself:"
        if result.cover_letter_pdf is not None:
            base = f"{_company_slug(result.company_name)}_cover_letter_{RESUME_OWNER}"
            files.append((f"{base}.pdf", result.cover_letter_pdf))
        elif result.status:
            message += f"\n_Cover-letter PDF skipped: {result.status}_"
        if result.warnings:
            message += (
                "\n⚠️ Possible overstatements in the cover letter — review before "
                f"sending: {', '.join(result.warnings)}."
            )
        return CommandResult(message=message, files=files)

    await _run_with_status(
        interaction,
        run,
        status_title=f"📝 Drafting an application for <{url}>…",
        error_text="Something went wrong while drafting the application. Check the bot logs for details.",
    )


@tree.command(name="tailor", description="Tailor your LaTeX resume to a specific job posting.")
@app_commands.describe(
    job_url="Link to the job posting to tailor your resume toward",
    resume_tex="Your resume's LaTeX source as a .tex file",
)
async def tailor(
    interaction: discord.Interaction,
    job_url: str,
    resume_tex: discord.Attachment,
):
    if await _reject_invalid_tex(interaction, resume_tex):
        return

    if not re.match(r"https?://\S+", job_url.strip(), re.IGNORECASE):
        await interaction.response.send_message(
            "Please provide a valid job posting link starting with `http(s)://`.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    logger.info(
        "tailor invoked by %s (url=%s, tex=%s)",
        interaction.user, job_url, resume_tex.filename,
    )

    latex_source = await _read_text_attachment(interaction, resume_tex)
    if latex_source is None:
        return

    url = job_url.strip()

    def run():
        result = tailor_resume(job_url=url, latex_source=latex_source)
        # Name files after the hiring company, e.g. Acme_resume_<owner>.pdf.
        base = f"{_company_slug(result.company_name)}_resume_{RESUME_OWNER}"
        files = [(f"{base}.tex", result.latex_source.encode("utf-8"))]
        if result.pdf_bytes is not None:
            files.append((f"{base}.pdf", result.pdf_bytes))

        changes = "\n".join(f"- {c}" for c in result.change_summary) or "- (no changes reported)"
        message = (
            f"Here's your tailored resume for <{url}>.\n"
            f"**Changes:**\n{changes}\n\n_{result.status}_"
        )
        if result.warnings:
            message += (
                "\n⚠️ Possible overstatements in the resume — review before "
                f"sending: {', '.join(result.warnings)}."
            )
        # Keep room for the mention prefix under Discord's 2000-char limit.
        if len(message) > 1900:
            message = message[:1900] + "…"
        return CommandResult(message=message, files=files)

    await _run_with_status(
        interaction,
        run,
        status_title=f"✂️ Tailoring your resume for <{url}>…",
        error_text="Something went wrong while tailoring the resume. Check the bot logs for details.",
    )


def _is_pdf(resume: discord.Attachment) -> bool:
    filename = (resume.filename or "").lower()
    return resume.content_type in ("application/pdf", None) or filename.endswith(".pdf")


def _company_slug(name: str) -> str:
    """Filesystem-safe token from a company name, e.g. 'Acme Corp' -> 'Acme_Corp'."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_")
    return slug or "company"


async def _reject_invalid_resume(interaction: discord.Interaction, resume: discord.Attachment) -> bool:
    """Validate the resume attachment. Returns True (and sends an ephemeral
    error) if it's unusable. Must be called *before* the interaction is deferred,
    since it responds via the initial interaction response.
    """
    if not _is_pdf(resume):
        await interaction.response.send_message(
            "Please attach your resume as a **PDF** file.", ephemeral=True
        )
        return True
    if resume.size > MAX_RESUME_BYTES:
        await interaction.response.send_message(
            "That resume is too large — please attach a PDF under 10 MB.", ephemeral=True
        )
        return True
    return False


async def _read_resume_data(interaction: discord.Interaction, resume: discord.Attachment) -> str | None:
    """Download + base64-encode the resume PDF after the interaction is deferred.

    Read up front because the attachment's CDN URL is short-lived. Returns None
    (notifying the user) on failure.
    """
    try:
        resume_bytes = await resume.read()
    except discord.HTTPException:
        logger.exception("Failed to read resume attachment for %s", interaction.user)
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                "Couldn't read the attached resume — please try again.", ephemeral=True
            )
        return None
    return encode_pdf_bytes(resume_bytes)


async def _reject_invalid_tex(interaction: discord.Interaction, tex: discord.Attachment) -> bool:
    """Validate the .tex attachment. Returns True (and sends an ephemeral error)
    if it's unusable. Must be called before the interaction is deferred.
    """
    if not (tex.filename or "").lower().endswith(".tex"):
        await interaction.response.send_message(
            "Please attach your resume's LaTeX source as a **.tex** file.", ephemeral=True
        )
        return True
    if tex.size > MAX_TEX_BYTES:
        await interaction.response.send_message(
            "That .tex file is too large — please attach one under 1 MB.", ephemeral=True
        )
        return True
    return False


async def _read_text_attachment(interaction: discord.Interaction, attachment: discord.Attachment) -> str | None:
    """Download a text attachment and decode it as UTF-8 after the interaction is
    deferred. Returns None (notifying the user) on failure.
    """
    try:
        data = await attachment.read()
    except discord.HTTPException:
        logger.exception("Failed to read attachment for %s", interaction.user)
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                "Couldn't read the attached file — please try again.", ephemeral=True
            )
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                "Couldn't read that file as UTF-8 text — is it really a .tex source file?",
                ephemeral=True,
            )
        return None


async def _run_with_status(
    interaction: discord.Interaction,
    run,
    *,
    status_title: str,
    error_text: str,
):
    """Run a blocking pipeline callable off the event loop while streaming its
    logs into a live-updating Discord message, then deliver its CommandResult.

    `run` returns a CommandResult (a message plus zero or more files). The status
    message and final result both go to the channel via channel.send (bot token,
    no expiry) rather than interaction.followup, whose token dies after 15
    minutes — longer than a run can take. A short ephemeral followup just clears
    the invoker's "thinking..." state.
    """
    channel = interaction.channel
    mention = interaction.user.mention

    with contextlib.suppress(discord.HTTPException):
        await interaction.followup.send(
            "Started — I'll stream progress and post the result in this channel.",
            ephemeral=True,
        )

    status_msg = None
    if channel is not None:
        with contextlib.suppress(discord.HTTPException):
            status_msg = await channel.send(status_title)

    # Mirror app logs into the status message via a handler + interval editor.
    handler = DiscordStatusHandler(asyncio.get_running_loop())
    handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    async def _pump_status():
        while True:
            await asyncio.sleep(_STATUS_REFRESH_SECONDS)
            if status_msg is not None and handler.dirty:
                handler.dirty = False
                with contextlib.suppress(discord.HTTPException):
                    await status_msg.edit(content=handler.render())

    pump_task = asyncio.create_task(_pump_status())

    try:
        result = await asyncio.to_thread(run)
    except Exception:
        logger.exception("command failed for %s", interaction.user)
        await _send_result(channel, content=f"{mention} {error_text}")
        return
    finally:
        # Stop the pump, detach the handler, and flush any remaining log lines.
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
        root_logger.removeHandler(handler)
        if status_msg is not None:
            with contextlib.suppress(discord.HTTPException):
                await status_msg.edit(content=handler.render())

    # Deliver the message plus any files (long output goes in files to dodge
    # Discord's 2000-character message limit).
    files = [
        discord.File(io.BytesIO(data), filename=name)
        for name, data in result.files
    ]
    await _send_result(channel, content=f"{mention} {result.message}", files=files)


async def _send_result(channel, **kwargs):
    """Send the final message to the channel using the bot token.

    Used instead of interaction.followup so delivery still works after the
    interaction's 15-minute token has expired.
    """
    if channel is None:
        logger.error("No channel available to deliver result")
        return
    try:
        await channel.send(**kwargs)
    except discord.HTTPException:
        logger.exception("Failed to deliver result to channel")


def main():
    setup_logging()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set (add it to your .env file).")
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
