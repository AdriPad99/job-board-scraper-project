"""Compile LaTeX source to a PDF using the system pdflatex (TeX Live).

Kept deliberately small: write the source to a temp dir, run pdflatex once, and
return the PDF bytes or a trimmed error log. Shell-escape is left disabled (the
pdflatex default) so \\write18 can't run arbitrary commands from the source.
"""

import os
import re
import shutil
import subprocess
import tempfile

from logger import get_logger

logger = get_logger(__name__)


_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_LATEX_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _LATEX_ESCAPES))

# Minimal letter document: 1in margins, no indent, blank line between paragraphs.
# Uses only base LaTeX + geometry so it compiles on a lean TeX Live install.
_COVER_LETTER_HEAD = (
    "\\documentclass[11pt]{article}\n"
    "\\usepackage[margin=1in]{geometry}\n"
    "\\setlength{\\parindent}{0pt}\n"
    "\\setlength{\\parskip}{1em}\n"
    "\\pagestyle{empty}\n"
    "\\begin{document}\n"
)
_COVER_LETTER_TAIL = "\n\\end{document}\n"


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain text (single pass, so inserted
    escape sequences are not themselves re-escaped)."""
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPES[m.group()], text)


def cover_letter_to_latex(text: str) -> str:
    """Wrap plain cover-letter text in a minimal, escaped LaTeX document.

    Blank lines become paragraph breaks; single newlines inside a paragraph
    (e.g. the sign-off) become forced line breaks.
    """
    paragraphs = re.split(r"\n\s*\n", text.strip())
    rendered = []
    for para in paragraphs:
        escaped = escape_latex(para.strip())
        escaped = escaped.replace("\n", " \\\\\n")
        rendered.append(escaped)
    return _COVER_LETTER_HEAD + "\n\n".join(rendered) + _COVER_LETTER_TAIL


class LatexNotInstalled(Exception):
    """Raised when no pdflatex binary is available on the host."""


def _error_tail(log: str, limit: int = 1500) -> str:
    """Extract the useful part of a pdflatex log.

    pdflatex prints errors as lines beginning with '!', so prefer the region
    around the first such line; otherwise fall back to the tail of the log.
    """
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("!"):
            return "\n".join(lines[i:i + 20])[:limit]
    return log[-limit:]


def compile_latex(source: str, timeout: int = 120) -> tuple[bytes | None, str]:
    """Compile LaTeX source to PDF bytes.

    Returns (pdf_bytes, "") on success, or (None, error_tail) on failure.
    Raises LatexNotInstalled if pdflatex is not on PATH.
    """
    if shutil.which("pdflatex") is None:
        raise LatexNotInstalled("pdflatex not found on PATH")

    with tempfile.TemporaryDirectory() as workdir:
        tex_path = os.path.join(workdir, "resume.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(source)

        logger.debug("Compiling LaTeX (%d chars) in %s", len(source), workdir)
        try:
            proc = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning("pdflatex timed out after %ss", timeout)
            return None, f"pdflatex timed out after {timeout}s"

        pdf_path = os.path.join(workdir, "resume.pdf")
        if proc.returncode == 0 and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                return f.read(), ""

        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return None, _error_tail(log)
