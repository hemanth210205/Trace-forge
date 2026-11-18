#!/bin/sh
# Start command for the Docker image on Render (see render.yaml and DEPLOY.md, "Render").
#
# Render sets PORT (default 10000) and the service's public address in RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME.
# Unless they are set explicitly (for example for a custom domain), Traceforge's public URL, allowed host name and
# trusted browser origin default to that address. Nothing secret is set or printed here.
set -eu

: "${TRACEFORGE_PUBLIC_URL:=${RENDER_EXTERNAL_URL:-}}"
: "${TRACEFORGE_ALLOWED_HOSTS:=${RENDER_EXTERNAL_HOSTNAME:-}}"
: "${TRACEFORGE_CORS_ORIGINS:=${RENDER_EXTERNAL_URL:-}}"

if [ -z "$TRACEFORGE_PUBLIC_URL" ] || [ -z "$TRACEFORGE_ALLOWED_HOSTS" ]; then
	echo "Refusing to start: set TRACEFORGE_PUBLIC_URL and TRACEFORGE_ALLOWED_HOSTS (Render sets RENDER_EXTERNAL_URL" \
		"and RENDER_EXTERNAL_HOSTNAME automatically)." >&2
	exit 2
fi
export TRACEFORGE_PUBLIC_URL TRACEFORGE_ALLOWED_HOSTS TRACEFORGE_CORS_ORIGINS

cd /app/backend
exec python -m app.serve --database main --host 0.0.0.0 --port "${PORT:-10000}"
