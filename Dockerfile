FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/data \
    && chown -R app:app /app

COPY pyproject.toml ./
COPY README.md LICENSE ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install .

USER app

