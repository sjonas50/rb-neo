# Minimal image for the rb-neo CLI. Neo4j runs separately (see docker-compose.yml).
FROM python:3.11-slim

# Install uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache -e .

# The word corpus is mounted at runtime (it is large and gitignored):
#   docker run --rm -e NEO4J_URI=bolt://host.docker.internal:7687 \
#     -v "$PWD/words:/app/words" rb-neo demo
ENTRYPOINT ["rb-neo"]
CMD ["--help"]
