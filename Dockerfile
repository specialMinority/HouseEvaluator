FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    HOUSE_EVALUATOR_DB=/data/v2.sqlite3 \
    HOUSE_EVALUATOR_DEMO=0 \
    HOUSE_EVALUATOR_LEGACY=0 \
    SUUMO_LIVE=0

WORKDIR /app

# Local v2: personal public search and optional feeds; legacy collectors excluded.
COPY backend/__init__.py ./backend/__init__.py
COPY backend/src/server.py ./backend/src/server.py
COPY backend/v2 ./backend/v2
COPY frontend/v2 ./frontend/v2
COPY scripts/import_v2_snapshot.py scripts/run_v2_ingestion.py scripts/maintain_v2_store.py scripts/validate_v2_market.py scripts/check_v2_release.py scripts/load_test_v2.py scripts/drill_v2_restore.py ./scripts/
COPY scripts/check_v2_alerts.py scripts/drill_v2_alerts.py ./scripts/

RUN groupadd --gid 10001 houseevaluator \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin houseevaluator \
    && mkdir -p /data /backups \
    && chown 10001:10001 /data /backups

USER 10001:10001
VOLUME ["/data", "/backups"]
EXPOSE 8080

CMD ["python", "-m", "backend.src.server"]
