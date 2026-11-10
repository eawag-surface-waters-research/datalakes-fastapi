ARG PYTHON_IMAGE=python:3.14-slim
ARG RCLONE_IMAGE=rclone/rclone:1.71.2

# to copy rclone binary from the rclone image
FROM ${RCLONE_IMAGE} AS rclone

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
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
ENV PATH="/code/.venv/bin:$PATH"
COPY ./app /code/app
COPY ./alembic.ini /code/alembic.ini
COPY ./migrations /code/migrations
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--no-server-header"]
