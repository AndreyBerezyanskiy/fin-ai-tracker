FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKER_HEARTBEAT_FILE=/tmp/family-finance-worker.heartbeat

RUN groupadd --gid 10001 appuser \
    && useradd --no-log-init --uid 10001 --gid 10001 --create-home appuser

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels family-finance-tracker

COPY alembic.ini ./
COPY migrations ./migrations

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["family-finance-healthcheck"]

STOPSIGNAL SIGTERM
CMD ["family-finance-bot"]
