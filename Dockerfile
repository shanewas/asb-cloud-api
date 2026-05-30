FROM python:3.11-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt
RUN playwright install chromium --with-deps

FROM python:3.11-slim
# Install curl for healthchecks (used by docker-compose healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY asb_api/ ./asb_api/
COPY config.yaml .
EXPOSE 8000
ENV PYTHONPATH=/app
CMD ["python", "-m", "asb_api"]
