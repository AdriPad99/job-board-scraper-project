import os
import base64
import anthropic
import instructor

from models import JobList, JobDetails, AppliableJob
from dotenv import load_dotenv

from logger import get_logger

load_dotenv()

logger = get_logger(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
claude = instructor.from_anthropic(anthropic_client)

def encode_pdf(path: str) -> str:
    """Base64-encode a PDF once so it can be reused across many requests."""
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def call_claude(
    prompt: str,
    history: list[dict],
    model: JobList | JobDetails | None,
    system: str | None = None,
) -> JobList | JobDetails | None:

    history.append({"role":"user", "content": prompt})

    logger.debug("Calling Claude (messages=%d, structured=%s)", len(history), bool(model))
    kwargs = dict(
        max_tokens=4096,
        model="claude-sonnet-4-5",
        messages=history,
        response_model=model,
    )
    if system is not None:
        kwargs["system"] = system
    response = claude.messages.create(**kwargs)
    logger.debug("Claude response received")

    return response


def call_claude_with_resume(
    resume_data: str,
    prompt: str,
    system: str | None = None,
    model: AppliableJob | None = None,
) -> AppliableJob:

    # cache_control marks the breakpoint after the (stable) resume document, so
    # the system prompt + resume are cached across the per-job loop and only the
    # varying posting text is billed at full input price on each call.
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": resume_data,
                    },
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    kwargs = dict(max_tokens=4096, model="claude-sonnet-4-5", messages=messages)
    if system is not None:
        kwargs["system"] = system

    logger.debug("Calling Claude with resume (structured=%s)", bool(model))
    if model is not None:
        response = claude.messages.create(**kwargs, response_model=model)
        logger.debug("Claude response received")
        return response

    response = anthropic_client.messages.create(**kwargs)
    logger.debug("Claude response received")

    return response.content[0].text