FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/data \
    && chown -R app:app /app

# Include the package metadata and release documentation required by the build.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install .

USER app

EXPOSE 8000

# Default process for the image: the local/private FastAPI service. Override it
# to run the CLI instead, for example:
#   docker compose run --rm tracker wallapop-track list
CMD ["uvicorn", "wallapop_tracker.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
