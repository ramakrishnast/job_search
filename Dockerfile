# syntax=docker/dockerfile:1.7
# ---------- frontend build ----------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend/dist \
    COMPANIES_FILE=/app/config/companies.yaml \
    COMPANIES_LOCAL_FILE=/app/config/companies.local.yaml \
    FILTERS_FILE=/app/config/filters.yaml \
    PROFILE_FILE=/app/config/profile.yaml \
    PROFILE_EXAMPLE_FILE=/app/config/profile.example.yaml
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY config/ /app/config/
COPY --from=web /web/dist /app/frontend/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN useradd --create-home --uid 10001 jobpilot && mkdir -p /data && chown -R jobpilot:jobpilot /data \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
# The entrypoint fixes ownership of the mounted /data volume, then runs the app as the non-root `jobpilot` user.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
# Single worker on purpose: the in-process scheduler must run exactly once, and SQLite has one writer.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
