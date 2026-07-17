FROM python:3.11-slim

# Ensure logs are flushed immediately (no buffering)
ENV PYTHONUNBUFFERED=1

# System deps for PyMuPDF, Playwright, lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    libgl1 \
    libglib2.0-0 \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy project metadata first for layer caching
COPY pyproject.toml requirements.txt ./

# Sync deps via uv
RUN uv venv && uv pip install -r requirements.txt

# Install Playwright browsers + deps (skip building this project)
RUN uv run --no-project playwright install --with-deps chromium

# Copy source
COPY src/ ./src/
COPY wait-for-grobid.sh /usr/local/bin/wait-for-grobid.sh
RUN chmod +x /usr/local/bin/wait-for-grobid.sh

ENTRYPOINT ["/usr/local/bin/wait-for-grobid.sh"]
CMD ["uv", "run", "--no-project", "python", "-m", "src.main"]