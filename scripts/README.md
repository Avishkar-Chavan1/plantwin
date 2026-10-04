# ProcessTwin PostgreSQL Backup and Restore

This directory contains scripts for backing up and restoring the ProcessTwin PostgreSQL database.

## Prerequisites

- PostgreSQL client tools (`pg_dump`, `pg_restore`, `psql`, `dropdb`, `createdb`)
- Access to the PostgreSQL database (credentials in `DATABASE_URL`)
- `gzip` for compression

## Backup Script

### `scripts/backup_postgres.sh`

Creates a compressed, timestamped backup of the ProcessTwin database.

`DATABASE_URL` accepts every form the application accepts, including the SQLAlchemy
`postgresql+psycopg://` scheme (the driver suffix is stripped before libpq tools see it)
and URLs without an explicit port.

```bash
# Using DATABASE_URL from .env
./scripts/backup_postgres.sh

# Or with explicit DATABASE_URL
DATABASE_URL=postgresql://user:pass@host:5432/processtwin ./scripts/backup_postgres.sh
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | From `.env` or environment |
| `BACKUP_DIR` | Directory to store backups | `./backups` |
| `RETENTION_DAYS` | Days to keep backups | `30` |

### Output

Backups are stored as `processtwin_<database>_<timestamp>.dump.gz` in the backup directory.

## Restore Script

### `scripts/restore_postgres.sh`

Restores a ProcessTwin database from a backup file.

```bash
# Using DATABASE_URL from .env
./scripts/restore_postgres.sh ./backups/processtwin_processtwin_20261003_120000.dump.gz

# Or with explicit DATABASE_URL
./scripts/restore_postgres.sh ./backups/processtwin_processtwin_20261003_120000.dump.gz postgresql://user:pass@host:5432/processtwin
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | From `.env` or environment |
| `CONFIRM` | Skip confirmation prompt (`yes`) | Requires interactive confirmation |

### What the Restore Does

1. **Confirms** the destructive operation (unless `CONFIRM=yes`)
2. **Drops** the existing database
3. **Creates** a fresh database
4. **Restores** from the backup file using `pg_restore`
5. **Runs** Alembic migrations to ensure schema is current

> **Post-restore step:** backups are taken with `--no-privileges` for portability,
> so grants to the non-owner application role are intentionally NOT in the archive.
> Re-apply them after every restore (CI enforces this step):
>
> ```bash
> psql "$DATABASE_ADMIN_URL" -v app_role=processtwin_app \
>   -v app_password='...' -f scripts/postgres_app_role.sql
> ```
>
> Run restore itself with the **owner** URL — it drops/creates databases and runs DDL.

## `scripts/postgres_app_role.sql`

Provisions the non-owner application role that makes PostgreSQL row-level security
enforceable (table owners bypass RLS). Idempotent; role name and password are passed
as psql variables so no credentials live in the file:

```bash
psql "$DATABASE_ADMIN_URL" -v app_role=processtwin_app \
  -v app_password='replace-with-a-secret' -f scripts/postgres_app_role.sql
```

The role receives `USAGE` on the schema and DML on existing and future tables —
never DDL, ownership, or `BYPASSRLS`. Grant it once after the first migration and
after every restore.

## Local Development Restore Procedure

For local development with Docker Compose:

```bash
# 1. Start the database
docker compose up -d postgres

# 2. Wait for database to be ready
docker compose exec postgres pg_isready -U processtwin -d processtwin

# 3. Run restore (uses default DATABASE_URL from compose)
CONFIRM=yes ./scripts/restore_postgres.sh ./backups/processtwin_processtwin_20261003_120000.dump.gz

# 4. Verify by running tests
python -m pytest tests/integration/test_migrations.py -v
```

## Production Considerations

**These scripts are for local development and testing only.** For production deployments:

1. **Use managed backup solutions:**
   - AWS RDS: Automated snapshots, point-in-time recovery
   - Google Cloud SQL: Automated backups, point-in-time recovery
   - Azure Database for PostgreSQL: Automated backups, geo-redundant storage
   - Timescale Cloud: Built-in continuous aggregates, retention policies

2. **Implement proper backup strategy:**
   - Daily full backups with point-in-time recovery
   - Cross-region replication for disaster recovery
   - Regular restore testing (at least quarterly)
   - Encrypted backups at rest and in transit

3. **Document your runbook:**
   - Recovery Time Objective (RTO)
   - Recovery Point Objective (RPO)
   - Escalation procedures
   - Contact information for cloud support

4. **Never rely solely on local scripts for production disaster recovery.**

## Backup Format

The backup uses PostgreSQL's custom format (`-Fc`) which provides:
- Compression by default
- Parallel restore capability (`pg_restore -j N`)
- Selective table restore
- Cross-version compatibility

## Verification

After restore, verify the database:

```bash
# Check table count
psql -d processtwin -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"

# Run migration tests
python -m pytest tests/integration/test_migrations.py -v

# Check data integrity
psql -d processtwin -c "SELECT count(*) FROM organizations;"
psql -d processtwin -c "SELECT count(*) FROM sensor_readings;"
```