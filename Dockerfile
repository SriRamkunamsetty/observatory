# Multi-stage production Dockerfile for Chat Observatory
# Build stage
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final production stage
FROM python:3.12-slim AS runner

LABEL org.opencontainers.image.title="Chat Observatory" \
      org.opencontainers.image.description="Enterprise OpenTelemetry LLM Observability & Regression Testing" \
      org.opencontainers.image.authors="SriRam Kunamsetty <mohansriramkunamsetty@gmail.com>" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/SriRamkunamsetty/observatory"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PATH="/home/app/.local/bin:$PATH"

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
    useradd --uid 10001 --create-home app && \
    mkdir -p /data && \
    chown -R 10001:10001 /data

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /root/.local /home/app/.local
COPY observatory ./observatory

RUN chown -R 10001:10001 /home/app /app

USER 10001
EXPOSE 8082

HEALTHCHECK --interval=20s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8082/healthz')" || exit 1

CMD ["gunicorn", "-b", "0.0.0.0:8082", "-w", "2", "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-", "observatory.server:create_app()"]
