#!/bin/sh
set -eu

# One API worker on one host: migrate before accepting requests.
alembic upgrade head

# Only Caddy can reach this service: the API has no published host port.
# Caddy replaces untrusted incoming forwarding headers before proxying.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
    --proxy-headers --forwarded-allow-ips='*' --limit-concurrency 32 \
    --timeout-keep-alive 5
