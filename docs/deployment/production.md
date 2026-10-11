# Deployment notes

## Render (Docker web service)

The production Dockerfile uses the repository root as its build context,
installs the root Python distribution and copies the API, local packages,
connectors and migrations. Prefer it on Render to avoid runtime build-command
ambiguity. Do not use `poetry install` or set the service root to `apps/api`:
the root package uses setuptools discovery for `apps*`, `packages*` and
`connectors*`; there is no top-level `processtwin` import package.

Configure the Render web service as follows:

| Setting | Value |
| --- | --- |
| Runtime | Docker |
| Root Directory | Leave blank (repository root) |
| Dockerfile Path | `Dockerfile.api.prod` |
| Docker Context Directory | `.` (repository root) |
| Build Command | Leave unset; Render builds the Dockerfile |
| Docker Command | `sh -c 'unset DATABASE_ADMIN_URL; exec gunicorn apps.api.processtwin_api.main:app --bind 0.0.0.0:$PORT --workers 1 --worker-class uvicorn.workers.UvicornWorker --access-logfile - --error-logfile - --log-level info'` |
| Health Check Path | `/ready` |

Set `ENVIRONMENT=production` and inject the production `DATABASE_URL`,
`JWT_SECRET`, `METRICS_TOKEN` and HTTPS `CORS_ORIGINS` as Render environment
variables. Do not set `DEMO_EMAIL` or `DEMO_PASSWORD` in production. Set
`DATABASE_URL` to the non-owner application role. Configure
`DATABASE_ADMIN_URL` as a secret for the pre-deploy migration. Render makes
service environment variables available to the pre-deploy and Docker commands;
the Docker command removes `DATABASE_ADMIN_URL` from its shell environment
before executing Gunicorn. Render runs the pre-deploy command on a separate instance
before deploying the image. Pre-deploy commands require a paid Render web
service. Set that command to:

```sh
DATABASE_URL="$DATABASE_ADMIN_URL" python -m apps.api.processtwin_api.migration_runner
```

This is the migration command already used in CI. The migration runner uses the
configured `DATABASE_URL`; run it as the table owner. Provision the non-owner
application role with `scripts/postgres_app_role.sql` as described in the
operations runbook. PostgreSQL table owners bypass RLS, so keep the running
API's `DATABASE_URL` on that non-owner role.

Start with one Gunicorn worker on a small staging instance to limit per-worker
memory use; increase only after measuring available memory and request load.

If using Render's native Python runtime instead, the repository root is still
required; use `python -m pip install .` and a Python 3.12-or-later runtime. The
production dependencies include both Gunicorn and Uvicorn.

After the first staging deploy, verify `/live` returns 200 and `/ready` returns
200 with `database: ok` (and `redis: not_configured` if Redis is not configured).
`/health` is informational and does not test dependencies. To prove database
tenant isolation, run the existing `tests/integration/test_postgres_rls.py`
against staging with `TEST_DATABASE_URL` set to the non-owner app role and
`TEST_DATABASE_ADMIN_URL` set to the owner role:

```sh
TEST_DATABASE_URL="$DATABASE_URL" \
TEST_DATABASE_ADMIN_URL="$DATABASE_ADMIN_URL" \
python -m pytest -q tests/integration/test_postgres_rls.py
```

That suite seeds test tenant rows, so do not run it against production.

For a small installation, deploy `docker-compose.prod.yml` behind an HTTPS reverse proxy, supply production secrets through a secret manager or injected environment, and schedule database/object-storage backups. Test restoration before describing recovery objectives.

For AWS, separate app containers (ECS/EKS) from RDS PostgreSQL/Timescale-compatible storage, ElastiCache Redis, S3/MinIO-compatible object storage, an ALB, CloudWatch/OpenTelemetry collection and Secrets Manager. Keep the deployment layer outside the physics and API packages so it cannot alter engineering behavior.

Neither topology authorizes direct plant control. Connectivity adapters must be separately assessed, allow-listed and configured by a customer engineer.
