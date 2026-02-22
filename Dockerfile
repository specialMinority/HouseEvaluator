FROM python:3.12-slim

WORKDIR /app

# Copy only what the API needs at runtime (specs + benchmark raw + frontend static).
COPY index.html ./index.html
COPY backend ./backend
COPY frontend ./frontend
COPY image ./image
COPY error_image ./error_image
COPY spec_bundle_v0.1.2 ./spec_bundle_v0.1.2
COPY agents/agent_D_benchmark_data/out ./agents/agent_D_benchmark_data/out

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080
ENV BENCHMARK_INDEX_PATH=/tmp/benchmark_index.json
ENV SUUMO_LIVE=1

EXPOSE 8080

CMD ["python", "-m", "backend.src.server"]
