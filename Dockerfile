# Discord bot image. Based on the official uv image (uv + Python preinstalled),
# with TeX Live added so /tailor and /apply can compile PDFs via pdflatex.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# System dependency: pdflatex (TeX Live). Without it the bot still runs, but
# /tailor and /apply skip PDF output. recommended + extra + fonts covers the
# templates the app generates and most user resume templates.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (own layer) so they're cached unless the lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Copy the application source.
COPY . .

# Long-running worker: a Discord bot connects out to the gateway, so there is no
# HTTP port to expose.
CMD ["uv", "run", "--no-sync", "python", "bot.py"]
