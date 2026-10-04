#!/usr/bin/env bash
# PostgreSQL backup script for ProcessTwin
# This script creates a compressed, timestamped backup of the ProcessTwin database.
# Usage: ./scripts/backup_postgres.sh [DATABASE_URL]
#
# Environment variables (optional, can be overridden by arguments):
#   DATABASE_URL   - PostgreSQL connection string (default: from .env or environment)
#   BACKUP_DIR     - Directory to store backups (default: ./backups)
#   RETENTION_DAYS - Number of days to keep backups (default: 30)
#
# DATABASE_URL accepts every form the application itself accepts:
#   postgresql://user:pass@host:5432/processtwin
#   postgresql+psycopg://user:pass@host:5432/processtwin   (driver suffix stripped for libpq)
#   URLs without an explicit port
#
# Example:
#   DATABASE_URL=postgresql://user:pass@localhost/processtwin ./scripts/backup_postgres.sh
#
# Note: This is a local development/test backup script. For production,
# use your cloud provider's managed backup solution (e.g., AWS RDS snapshots,
# Google Cloud SQL backups, Azure Database for PostgreSQL backups).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default configuration
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

# Parse DATABASE_URL from argument or environment
if [[ $# -gt 0 ]]; then
    DATABASE_URL="$1"
elif [[ -f "$PROJECT_ROOT/.env" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_ROOT/.env"
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL not set. Provide as argument or set in .env/environment." >&2
    exit 1
fi

# libpq only understands postgres:// and postgresql://; SQLAlchemy appends a driver
# suffix (postgresql+psycopg://) that must be removed before pg_dump sees it.
PGURL="$(printf '%s' "$DATABASE_URL" | sed -E 's#^(postgres(ql)?)\+[A-Za-z0-9]+://#\1://#')"

if [[ ! "$PGURL" =~ ^postgres(ql)?:// ]]; then
    echo "ERROR: DATABASE_URL must be a postgresql:// URL, got: $DATABASE_URL" >&2
    exit 1
fi

# Database name is the final path segment (query parameters, if any, are stripped).
PGDATABASE="${PGURL##*/}"
PGDATABASE="${PGDATABASE%%\?*}"
if [[ -z "$PGDATABASE" ]]; then
    echo "ERROR: DATABASE_URL does not name a database: $DATABASE_URL" >&2
    exit 1
fi

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
# .dump.gz = gzip-compressed pg_dump custom format (-Fc). Custom format is already
# internally compressed, so the outer gzip only adds a cheap extra layer and keeps the
# file portable for `gunzip -c file.dump.gz | pg_restore`.
BACKUP_FILE="$BACKUP_DIR/processtwin_${PGDATABASE}_${TIMESTAMP}.dump.gz"

mkdir -p "$BACKUP_DIR"

echo "Starting backup of database '$PGDATABASE'..."
echo "Backup file: $BACKUP_FILE"

# --no-owner / --no-privileges: portable across environments
# --clean --if-exists: restore can be replayed onto a non-empty target
# --format=custom: compressed, selectively restorable, parallelizable
pg_dump \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --format=custom \
    --file="$BACKUP_FILE.tmp" \
    "$PGURL"

gzip -f "$BACKUP_FILE.tmp"
mv "$BACKUP_FILE.tmp.gz" "$BACKUP_FILE"

# Verify backup: pg_restore -l reads the archive table of contents end-to-end.
if [[ -f "$BACKUP_FILE" && -s "$BACKUP_FILE" ]] && pg_restore --list "$BACKUP_FILE" >/dev/null; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    TABLES_IN_ARCHIVE=$(pg_restore --list "$BACKUP_FILE" | grep -c '^[0-9]' || true)
    echo "Backup completed successfully: $BACKUP_FILE ($BACKUP_SIZE, $TABLES_IN_ARCHIVE archive entries)"
else
    echo "ERROR: Backup file missing, empty, or unreadable: $BACKUP_FILE" >&2
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Clean up old backups
echo "Cleaning up backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "processtwin_${PGDATABASE}_*.dump.gz" -type f -mtime +"$RETENTION_DAYS" -delete

echo "Backup process complete."
