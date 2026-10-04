-- Provision the non-owner application role that makes PostgreSQL row-level
-- security actually enforceable.
--
-- Postgres table OWNERS bypass RLS unless policies are created with FORCE.
-- If the API connects with the same role that ran `alembic upgrade head`,
-- every `processtwin_*` policy is silently skipped. The production posture is:
--
--   DATABASE_ADMIN_URL  owner/migrator role  ->  `migrate` service / DBA tooling
--   DATABASE_URL        non-owner role       ->  api / train services
--
-- Usage (as the database owner, AFTER `alembic upgrade head`):
--
--   psql "$DATABASE_ADMIN_URL" \
--     -v app_role=processtwin_app \
--     -v app_password='replace-with-a-secret' \
--     -f scripts/postgres_app_role.sql
--
-- The role name/password are psql variables so no credentials live in this file.

\if :{?app_role}
\else
\echo 'ERROR: pass -v app_role=<name>'
\quit 1
\endif

\if :{?app_password}
\else
\echo 'ERROR: pass -v app_password=<secret>'
\quit 1
\endif

BEGIN;

-- Idempotent: re-running the script must not fail on an existing role.
-- format() with %I/%L quotes the identifiers safely; psql only substitutes
-- :'var' outside SQL string literals.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_role', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec

GRANT USAGE ON SCHEMA public TO :"app_role";

-- DML only: no DDL, no ownership, no BYPASSRLS, no superuser.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";

-- Tables created by future migrations (owner-created) inherit the same grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";

COMMIT;
