ARG PYTHON_IMAGE=python:3.14.10-slim-trixie
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data
LABEL org.projektakte.application="projektakte"
LABEL org.opencontainers.image.title="Projektakte"
LABEL org.opencontainers.image.description="Self-hosted project logbook for repairs, maintenance, and receipts."
LABEL org.opencontainers.image.source="https://github.com/nbf207/projektakte"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"
LABEL org.opencontainers.image.version="1.0.3"
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home akte
COPY . .
RUN DEMO_MODE=1 DATA_DIR=/tmp/akte-build python manage.py collectstatic --noinput && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
CMD ["python", "scripts/entrypoint.py", "--host", "0.0.0.0", "--port", "8000"]
