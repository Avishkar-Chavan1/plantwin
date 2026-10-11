# ProcessTwin — launch checklist

**Date:** 2026-10-10 · **Host:** Windows 11, Git Bash, Python 3.12.7, Node 24.21, no `make`,
no reachable Docker daemon.
**Scope:** startup MVP for demonstrations, early access and a controlled, read-only pilot.

Legend: **PASS** (verified here), **CI** (verified by a pipeline job that cannot run on this host),
**PARTIAL**, **NOT DONE**, **MANUAL** (requires your action).

## A. Acceptance criteria

| # | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Production frontend build succeeds | **PASS** | `cd apps/web && next build` compiled 19 routes; `/` landing prerendered 165 B. `node scripts/verify-build.mjs` → "17 pages OK (manifests present, no duplicate routes)". No `.next/server/app/(authenticated)/page_client-reference-manifest.js` error. |
| 2 | Backend tests, lint, type checks pass | **PASS** | Ruff 0 errors; `mypy` 0 errors in 73 files; `pytest -q` → **205 passed, 9 skipped** (skips are the PostgreSQL/Redis infra tests, run in CI). |
| 3 | App starts using documented steps | **PASS** | `alembic`-equivalent seed + `uvicorn` start; `/live` 200, `/health` 200, `/ready` 200. Documented in `README.md`. |
| 4 | Auth, authz, tenant isolation tested | **PASS** (app layer) / **CI** (RLS) | `test_authz_matrix.py`, `test_postgres_rls.py` (CI), token/refresh/logout integration tests pass; live login + `/auth/me` succeeded. |
| 5 | Dashboard data correctly sourced and labelled | **PASS** | Live `/api/v1/dashboard/summary` → `source_mode=SIMULATION`; UI shows SIMULATION / LIVE_READ_ONLY / HISTORICAL / MIXED_DATA_BLOCKED banners and freshness. |
| 6 | Simulation produces real model results and updates charts | **PASS** | Live `POST /api/v1/simulations` returned a 121-point physical trajectory (`model_validity=UNVALIDATED_REFERENCE`); `test_simulation_flow.py` passes; charts plot the returned `trajectory`. |
| 7 | CSV import validates, persists, displays | **PASS** | Live preview→import→explore: 3 rows / 6 measurements stored and explorable. 13 upload-flow tests pass, including malformed/empty/oversize/duplicate/unit-mismatch and unmapped-column reporting. |
| 8 | Recommendations obey safety and data-quality gates | **PASS** | `test_dashboard_and_advisory_gate.py` passes: no recommendation when data is stale/invalid/out of envelope; review decisions persisted and audited. |
| 9 | Connector behavior read-only and simulator-tested | **PASS** | `test_read_only_boundary.py`, `test_connector_readonly_boundary.py`, `test_opcua_connector.py`, `test_opcua_end_to_end.py`, `test_gateway_opcua_pipeline.py` pass. No write path exists. |
| 10 | Migrations and recovery instructions verified | **CI** | `test_postgres_rls.py` / `test_migrations.py` + backup/restore roundtrip run in the CI `postgres` job. Not runnable here (no PostgreSQL). |
| 11 | Production env vars + deployment config documented | **PASS** | `.env.example`, `docker-compose.prod.yml`, `k8s/`, and §C below. |
| 12 | Staging deployment passes smoke tests | **NOT DONE** | No staging environment has been deployed (requires your infrastructure and explicit approval). |
| 13 | No critical unresolved security / data-integrity / safety issue | **PASS** (known items) | See §E. No secrets in tracked files or build output; advisory boundary intact; data-integrity transparency gap fixed. |

**Verdict: READY FOR STAGING.** Every locally runnable gate is green and the code is ready to be
deployed to a staging environment. It is **not** "READY FOR CONTROLLED MVP PILOT" until criterion 12
(a real staging deployment passing the smoke test) is met, and it establishes **no** real-plant
validation.

## B. Commands run (exact) and results

```bash
# Backend
python -m ruff check apps packages connectors tests   # All checks passed!
python -m mypy apps packages connectors               # Success: no issues found in 73 source files
python -m pytest -q                                   # 205 passed, 9 skipped

# Frontend (apps/web)
NEXT_DISABLE_STANDALONE=1 NEXT_PUBLIC_API_URL=http://localhost:8000 next build
node scripts/verify-build.mjs                         # 17 pages OK
tsc --noEmit                                          # clean
next lint --max-warnings 0                            # no warnings or errors
node --experimental-strip-types --test tests/*.test.ts # 7 pass, 0 fail

# Live end-to-end (API on :8011, seeded SQLite demo)
python scripts/smoke_workflows.py                     # 10/10 workflows passed
```

`make` is unavailable on this host; the commands above are exactly what the Makefile targets run.
`docker compose build`, `docker compose -f docker-compose.prod.yml build` and the PostgreSQL
migration/RLS/backup jobs were **not** run here (no Docker daemon, no PostgreSQL) — they run in CI.

## C. Staging deployment (exact steps + required environment variables)

Two supported topologies. Both are read-only and advisory; neither enables plant control.

### C1. Frontend → Vercel (recommended)

1. Import the repository in Vercel.
2. Set **Root Directory** = `apps/web`. Vercel detects pnpm and uses `apps/web/vercel.json`.
3. Set environment variables (Production):
   - `NEXT_PUBLIC_API_URL` = `https://<your-api-host>` (bare HTTPS origin; the build fails loudly if missing or non-HTTPS).
   - `NEXT_PUBLIC_CONTACT_EMAIL` = your public demo contact (optional; never a secret).
   - Do **not** set backend secrets here. Secrets must never appear in a `NEXT_PUBLIC_*` variable.
4. Deploy. Verify the build log ends with the compiled route table (no
   `page_client-reference-manifest.js` error) and that `/` serves the landing page.

### C2. Backend + database (container / compose)

```bash
# 1. Provision managed PostgreSQL (TimescaleDB-compatible) and, if used, Redis + S3/MinIO.
# 2. Create the non-owner application role before the first migration:
psql "$DATABASE_ADMIN_URL" -v app_role=processtwin_app -v app_password='<generated>' \
  -f scripts/postgres_app_role.sql
# 3. Configure .env from .env.example, then:
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d           # runs `migrate`, then api + web
docker compose -f docker-compose.prod.yml ps              # migrate exited 0, api healthy
curl -fsS https://<your-api-host>/live                    # {"status":"alive", ...}
curl -fsS https://<your-api-host>/ready                   # database: ok
```

Required variables (all fail-fast; no insecure defaults):

| Variable | Notes |
| --- | --- |
| `ENVIRONMENT=production` | Enforced; refuses SQLite and demo credentials. |
| `DATABASE_URL` | `postgresql+psycopg://` **non-owner** app role (RLS enforced). |
| `DATABASE_ADMIN_URL` | Table owner; used only by the one-shot `migrate` service. |
| `JWT_SECRET` | ≥32 chars, random; placeholders rejected. |
| `METRICS_TOKEN` | ≥32 chars, random; separate from `JWT_SECRET`. |
| `CORS_ORIGINS` | Explicit HTTPS origin(s) of the frontend. |
| `NEXT_PUBLIC_API_URL` | Public HTTPS API origin (build-time, frontend). |
| `NEXT_PUBLIC_CONTACT_EMAIL` | Optional public demo contact (frontend). |
| `REDIS_URL`, `MINIO_*`, `MLFLOW_TRACKING_URI` | Optional; empty means disabled. |

Optional: `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `RATE_LIMIT_*`,
`MAX_UPLOAD_BYTES`, `LOG_LEVEL`.

### C3. Staging smoke test (after both are up)

```bash
python scripts/smoke_workflows.py https://<api-host> <staging-user-email> <password>
```

### Rollback

- **Frontend:** redeploy the previous good deployment in Vercel (instant).
- **Backend:** redeploy the previous image tag (`REGISTRY/TAG`); database migrations are linear —
  do not downgrade a shared database unless the release notes say it is safe.
- **Database:** restore the most recent `backups/*.dump.gz` with
  `CONFIRM=yes scripts/restore_postgres.sh <archive> "<libpq-url>"`, then re-apply
  `scripts/postgres_app_role.sql` (the dump omits grants).

## D. Clean-clone setup (developer)

```bash
git clone <repo> && cd plantwin
python -m pip install -e ".[dev,parquet,mqtt,opcua]"
cp .env.example .env        # set JWT_SECRET and METRICS_TOKEN (32+ chars)
python -m apps.simulator.processtwin_simulator.seed_demo --hours 168
python -m uvicorn apps.api.processtwin_api.main:app --port 8000
# frontend:
cd apps/web && npm install && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

`.env.example` is validated at startup: placeholder `JWT_SECRET`/`METRICS_TOKEN` are **refused**.

## E. Security summary and unresolved risks

Verified here: no tracked `.env`; no AWS/GitHub/Slack/private-key patterns in tracked files or in
`apps/web/.next`; the read-only boundary has regression tests; production config is fail-closed.
Verified in CI: dependency audit (`pip-audit`), production image build, PostgreSQL RLS, backup/restore.

Unresolved (documented, not blocking staging): no OIDC/SSO; in-process (not shared) rate limiting;
no threat model, SBOM or independent penetration test; connector credential rotation not proven;
Docker/DB gates not reproducible on this host; **no plant validation of any kind**.

## F. MANUAL — you must configure or decide

- [ ] Provision managed PostgreSQL/TimescaleDB and run `scripts/postgres_app_role.sql` before the first migration.
- [ ] Decide `DATABASE_ADMIN_URL` handling and store secrets in a manager (never in the repo or an image).
- [ ] Import the repo to Vercel with Root Directory `apps/web`; set `NEXT_PUBLIC_API_URL` (and optional `NEXT_PUBLIC_CONTACT_EMAIL`).
- [ ] Deploy the API image (compose or k8s) and set `CORS_ORIGINS` to the real frontend origin.
- [ ] Create a staging user and run `scripts/smoke_workflows.py` against staging; record the output.
- [ ] Schedule and test a database backup/restore in the staging environment.
- [ ] If a plant pilot is planned: stand up an isolated, read-only edge gateway (no write path) and update `docs/ROADMAP_EXTERNAL.md`.
- [ ] Commission any HAZOP, plant-data authorisation and independent security review you require.

## G. Known limitations

Single CSTR reference process only; demonstrator model calibrated to synthetic data; advisory only;
OPC UA/MQTT proven against simulators, not customer plants; OIDC and shared rate limiting not
implemented; no certification, audit or plant validation is claimed or implied.
