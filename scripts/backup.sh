#!/bin/sh
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="backups/$stamp"
mkdir -p "$destination"
# PostgreSQL custom archive format; pg_restore can verify and restore it.
docker compose --env-file .env.production -f compose.production.yml exec -T db \
    pg_dump -U fridge -d fridge --format=custom > "$destination/database.dump"
docker compose --env-file .env.production -f compose.production.yml exec -T api \
    tar -C /app/uploads -czf - . > "$destination/uploads.tar.gz"
sha256sum "$destination/database.dump" "$destination/uploads.tar.gz" > "$destination/SHA256SUMS"
printf 'Backup saved in %s. Copy it to storage outside this server.\n' "$destination"
