import os
import base64
import anthropic
import instructor

from models import JobList, JobDetails, AppliableJob, Prettyizer
from dotenv import load_dotenv

from logger import get_logger

load_dotenv()

logger = get_logger(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
claude = instructor.from_anthropic(anthropic_client)

# Model tiers. Judgment- and writing-heavy calls (resume qualification, drafting,
# resume tailoring) stay on Sonnet; the high-volume markdown -> JSON extraction
# calls (URL lists, per-job descriptions) run on the much cheaper Haiku tier,
# which handles pure structuring work without a quality hit. Override per call
# via the model_id parameter below.
DEFAULT_MODEL = "claude-sonnet-4-5"
EXTRACTION_MODEL = "claude-haiku-4-5"

def encode_pdf(path: str) -> str:
    """Base64-encode a PDF once so it can be reused across many requests."""
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def encode_pdf_bytes(data: bytes) -> str:
    """Base64-encode raw PDF bytes (e.g. a Discord attachment) for reuse."""
    return base64.standard_b64encode(data).decode("utf-8")


def call_claude(
    prompt: str,
    history: list[dict],
    model: JobList | JobDetails | Prettyizer | None,
    system: str | None = None,
    model_id: str = DEFAULT_MODEL,
) -> JobList | JobDetails | Prettyizer | None:

    history.append({"role":"user", "content": prompt})

    logger.debug(
        "Calling Claude (model=%s, messages=%d, structured=%s)",
        model_id, len(history), bool(model),
    )
    # URL/job lists (Indeed combines up to 3 pages) and the final markdown can
    # run long, so give generous headroom. Kept under ~21.3k because above that
    # the Anthropic SDK requires streaming for non-streaming calls
    # (expected_time = 3600 * max_tokens / 128000 must stay under 600s).
    kwargs = dict(
        max_tokens=16000,
        model=model_id,
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
    max_tokens: int = 4096,
    model_id: str = DEFAULT_MODEL,
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

    kwargs = dict(max_tokens=max_tokens, model=model_id, messages=messages)
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