# One small image for the one-shot jobs that run before and beside the services:
#   migrate         alembic upgrade head against app_meta
#   keycloak-setup  the identity provider's realm structure (and, in the demo, sample users)
# Build from the repository root:  docker build -f deploy/setup.Dockerfile .
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY db/requirements.txt ./db/requirements.txt
RUN pip install --no-cache-dir -r db/requirements.txt httpx
COPY db/alembic.ini ./db/alembic.ini
COPY db/migrations ./db/migrations
COPY deploy/keycloak/bootstrap_dev.py ./deploy/keycloak/bootstrap_dev.py
USER app
ENV PYTHONUNBUFFERED=1
