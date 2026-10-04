#!/usr/bin/env bash
# PostgreSQL restore script for ProcessTwin
# This script restores a ProcessTwin database from a backup file.
# Usage: ./scripts/restore_postgres.sh BACKUP_FILE [DATABASE_URL]
#
# Environment variables (optional, can be overridden by arguments):
#   DATABASE_URL - PostgreSQL connection string (default: from .env or environment)
#   CONFIRM      - Set to "yes" to skip confirmation prompt (default: requires confirmation)
#
# Handles the same URL forms as backup_postgres.sh, including
# postgresql+psycopg:// (driver suffix stripped before libpq tools see it) and
# URLs without an explicit port. Requires PostgreSQL 13+ (DROP DATABASE ... WITH (FORCE)).
#
# Example:
#   DATABASE_URL=postgresql://user:pass@localhost/processtwin ./scripts/restore_postgres.sh ./backups/processtwin_processtwin_20261003_120000.dump.gz
#
# WARNING: This will DESTROY all existing data in the target database!
#          Use with extreme caution. This is a local development/test restore script.
#          For production disaster recovery, follow your organization's runbook.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 BACKUP_FILE [DATABASE_URL]" >&2
    echo "Example: $0 ./backups/processtwin_processtwin_20261003_120000.dump.gz" >&2
    exit 1
fi

BACKUP_FILE="$1"

# Parse DATABASE_URL from argument or environment
if [[ $# -gt 1 ]]; then
    DATABASE_URL="$2"
elif [[ -f "$PROJECT_ROOT/.env" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_ROOT/.env"
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL not set. Provide as argument or set in .env/environment." >&2
    exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

# Accept both the plain and the SQLAlchemy driver-suffixed URL scheme.
PGURL="$(printf '%s' "$DATABASE_URL" | sed -E 's#^(postgres(ql)?)\+[A-Za-z0-9]+://#\1://#')"

if [[ ! "$PGURL" =~ ^postgres(ql)?:// ]]; then
    echo "ERROR: DATABASE_URL must be a postgresql:// URL, got: $DATABASE_URL" >&2
    exit 1
fi

PGDATABASE="${PGURL##*/}"
PGDATABASE="${PGDATABASE%%\?*}"
if [[ -z "$PGDATABASE" ]]; then
    echo "ERROR: DATABASE_URL does not name a database: $DATABASE_URL" >&2
    exit 1
fi

# Connect to the maintenance database to drop/create the target database.
# Query parameters (e.g. sslmode=require) are preserved for the admin connection.
PGQUERY=""
if [[ "$PGURL" == *\?* ]]; then
    PGQUERY="?${PGURL#*\?}"
fi
ADMIN_URL="${PGURL%%\?*}"
ADMIN_URL="${ADMIN_URL%/*}/postgres${PGQUERY}"

echo "============================================================"
echo "WARNING: This will DESTROY all data in database '$PGDATABASE'"
echo "         and restore from: $BACKUP_FILE"
echo "============================================================"

if [[ "${CONFIRM:-}" != "yes" ]]; then
    read -r -p "Type 'yes' to confirm restore: " CONFIRM_INPUT
    if [[ "$CONFIRM_INPUT" != "yes" ]]; then
        echo "Restore cancelled."
        exit 1
    fi
fi

echo "Starting restore..."

# Drop and recreate the database so the restore starts from a clean state.
# WITH (FORCE) terminates lingering connections (PostgreSQL 13+).
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$PGDATABASE\" WITH (FORCE);"
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$PGDATABASE\";"

# Restore from the gzip-compressed custom-format archive.
# --clean --if-exists: replayable; --no-owner --no-privileges: portable across environments.
gunzip -c "$BACKUP_FILE" | pg_restore \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --dbname="$PGURL"

# Verify restore by checking table count
TABLE_COUNT=$(psql "$PGURL" -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" | xargs)
echo "Restore completed. Database '$PGDATABASE' now has $TABLE_COUNT tables."

# Run migrations to ensure schema is up to date
echo "Running Alembic migrations..."
cd "$PROJECT_ROOT"
DATABASE_URL="$DATABASE_URL" alembic upgrade head

echo "============================================================"
echo "Restore and migration completed successfully."
echo "============================================================"
