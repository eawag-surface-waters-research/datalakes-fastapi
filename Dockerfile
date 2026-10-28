ARG PYTHON_IMAGE=python:3.14-slim

# first stage: build the virtual environment with uv
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/
WORKDIR /code
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
COPY ./pyproject.toml ./uv.lock /code/
RUN uv sync --locked --no-dev --no-cache

# second stage: copy the virtual environment and run the app
FROM ${PYTHON_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* # install curl for healthcheck
WORKDIR /code
COPY --from=builder /code/.venv /code/.venv
ENV PATH="/code/.venv/bin:$PATH"
COPY ./app /code/app
CMD ["fastapi", "run", "app/main.py", "--port", "8000", "--workers", "4"]
