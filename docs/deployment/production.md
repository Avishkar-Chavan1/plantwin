# Deployment notes

## Render (Docker web service)

The production Dockerfile uses the repository root as its build context,
installs the root Python distribution and copies the API, local packages,
connectors and migrations. Prefer it on Render to avoid runtime build-command
ambiguity. Do not use `poetry install` or set the service root to `apps/api`:
the root package uses setuptools discovery for `apps*`, `packages*` and
`connectors*`; there is no top-level `processtwin` import package.

The deploy log `Running 'Dockerfile.api.prod'` means Render treated that text
as a start command. It did not select or build a Dockerfile. The Node.js version
message also indicates this service is currently configured for a native
Node.js runtime (likely using `apps/web/package.json`), not the API's Docker
runtime.

Configure the Render web service as follows:

| Setting | Value |
| --- | --- |
| Language | Docker (select this when creating the Web Service; do not select Node) |
| Root Directory | Leave blank (repository root) |
| Dockerfile Path | `Dockerfile.api.prod` |
| Build Command | Not used for Docker; Render builds the selected Dockerfile |
| Start Command | Not used for Docker |
| Docker Command | `python -m apps.api.processtwin_api.runtime` |
| Health Check Path | `/ready` |

In the Dashboard, set **Language** to **Docker** and enter the path above in
**Dockerfile Path**. Do not enter the Dockerfile path in **Start Command** or
**Docker Command**. Leave Root Directory at the repository root so the Docker
build context includes `pyproject.toml`, `apps/`, `packages/`, `connectors/`,
and `migrations/`. Leave the native Build and Start Command fields unused.
The Docker command invokes one launcher process. It runs the migrations with
`DATABASE_ADMIN_URL` before starting Gunicorn, then removes that variable from
the API process environment. The Gunicorn master starts one worker and binds to
Render's `$PORT`. Each service instance runs the launcher, while PostgreSQL's
existing advisory lock serializes concurrent migration attempts across instances.

Set `ENVIRONMENT=production` and inject the production `DATABASE_URL`,
`JWT_SECRET`, `METRICS_TOKEN` and HTTPS `CORS_ORIGINS` as Render environment
variables. Do not set `DEMO_EMAIL` or `DEMO_PASSWORD` in production. Set
`DATABASE_URL` to the non-owner application role and `DATABASE_ADMIN_URL` to
the table-owner migration role; both must target the same PostgreSQL database
and schema. Store both as Render environment variables, with the admin URL
marked secret. The launcher fails before API startup if either URL is missing,
non-PostgreSQL, or if migrations fail. It passes the admin URL as the
migration subprocess's `DATABASE_URL`, then starts the API using the non-owner
`DATABASE_URL` without exposing `DATABASE_ADMIN_URL` to Gunicorn. The same
launcher is the Docker image default, so it runs on Render Free without the
Pre-Deploy Command feature. The migration runner uses an advisory lock to
serialize startup migrations across service instances. Provision the non-owner
application role with `scripts/postgres_app_role.sql` as described in the
operations runbook. PostgreSQL table owners bypass RLS, so keep the running
API's `DATABASE_URL` on that non-owner role.

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
