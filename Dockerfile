# Traceforge: API and console in one image (used by render.yaml; see DEPLOY.md, "Render").
#
# Stage 1 builds the console with Node; stage 2 is a slim Python image that serves the API and the built console from
# one origin. Runs as an unprivileged user. Configuration comes only from environment variables: no .env file is
# copied (see .dockerignore).

FROM node:22.11.0-bookworm-slim AS console
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.11.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --system --uid 10001 --create-home --home-dir /home/traceforge traceforge
WORKDIR /app

# Dependencies first (pinned by constraints.txt) so code changes do not reinstall them.
COPY backend/pyproject.toml backend/constraints.txt backend/
COPY backend/app backend/app
RUN pip install -c backend/constraints.txt ./backend \
 && rm -rf backend/build backend/*.egg-info

COPY backend/alembic backend/alembic
COPY backend/alembic.ini backend/
COPY --from=console /src/frontend/dist frontend/dist
COPY deploy/render-start.sh deploy/render-start.sh

USER traceforge
EXPOSE 10000
CMD ["sh", "/app/deploy/render-start.sh"]
