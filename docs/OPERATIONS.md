# Operations runbook (starter)

Working instructions for operating a ProcessTwin deployment. Values here are
**starting points to be tuned and owned by your operations team**; formal SLOs,
on-call rotation and alert delivery channels are external commitments
(see `docs/ROADMAP_EXTERNAL.md`).

## Service map

| Service | Role | Health endpoint |
| --- | --- | --- |
| `api` (FastAPI + gunicorn) | REST API, metrics | `/live`, `/health`, `/ready`, `/metrics` |
| `web` (Next.js standalone) | Dashboard | `/` |
| `migrate` (one-shot) | advisory-lock-protected `alembic upgrade head` before deploys | exit code |
| `train` (one-shot, profile `training`) | Model retraining | exit code |

Probes:

- **Liveness** `/live` — process only; restart on repeated failure.
- **Readiness** `/ready` — checks database and configured Redis; returns 503 with
  per-dependency detail. Kubernetes uses it for both readiness and traffic gating.
- **Metrics** `/metrics` — requires `Authorization: Bearer $METRICS_TOKEN` when the
  token is configured. Scrape it, do not expose it publicly.

## Logging and correlation

- JSON-ish structured lines from logger `processtwin.api` with `request_completed`,
  `unhandled request error` events.
- Every response carries `X-Request-ID` (accepted from the caller if well-formed).
  When investigating, get the request ID first, then grep logs for it.
- Compose rotates container logs (`json-file`, 10 MB × 5). Ship logs to your SIEM
  with your own retention policy.

## Metrics and alerting

Rules: `observability/prometheus-rules.yml` (checked with `promtool check rules`).

```yaml
# prometheus.yml (minimal)
scrape_configs:
  - job_name: processtwin-api
    metrics_path: /metrics
    authorization:
      credentials_file: /run/secrets/metrics_token  # send as Bearer token
    static_configs:
      - targets: ["api:8000"]
rule_files:
  - /etc/prometheus/processtwin/prometheus-rules.yml
```

Alert classes:

| Alert | Severity | First action |
| --- | --- | --- |
| `ProcessTwinApiDown` | critical | container/pod status, then `/live` |
| `ProcessTwinNotReady` | warning | read `/ready` checks (DB, Redis) |
| `ProcessTwinHighErrorRate` | warning | API logs by request ID |
| `ProcessTwinLatencyHigh` | warning | slow paths, DB load, pool saturation |
| `ProcessTwinRateLimitedSustained` | warning | client overload vs credential stuffing |
| `ProcessTwinIngestionStalled` | warning | expected when idle; else upstream connectors |
| `ProcessTwinIngestionQualityDegraded` | warning | quality events, tag mapping review |
| `ProcessTwinHighMemory` | warning | large imports; OOM headroom before 1Gi limit |

Alertmanager routing (email/Slack/PagerDuty) is deployment-specific — configure
it in your Alertmanager and record the escalation path here:

- **Primary on-call:** _TBD — assign_
- **Secondary:** _TBD — assign_
- **Escalation timeout:** _TBD — e.g. 15 minutes for critical_

## Database posture

- **Two roles:** migrations run as the table owner (`DATABASE_ADMIN_URL`), the app
  connects as the non-owner (`DATABASE_URL`, provisioned by
  `scripts/postgres_app_role.sql`). This is what makes RLS enforcement real —
  owners bypass row-level security.
- Run `scripts/postgres_app_role.sql` before the first migration. Its default
  privileges ensure the owner-created tables are usable by the app role from the
  first API request; re-run it after restores or grant-policy changes.
- **Never** give the app role DDL rights or `BYPASSRLS`.
- Verify posture after provisioning:

  ```sql
  SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'processtwin_app';
  SELECT relname, relrowsecurity FROM pg_class
    WHERE relnamespace = 'public'::regnamespace AND relname IN ('plants','sensor_readings');
  ```

- Schema changes are migration-only: the API never calls `create_all` outside
  development/test (`Settings.auto_create_schema`).

## Backups and restore

- Backup: `scripts/backup_postgres.sh` (compressed `pg_dump -Fc`, verified with
  `pg_restore --list`). Suggested cadence: daily full + WAL/PITR if your platform
  supports it.
- Restore: `scripts/restore_postgres.sh <file> <owner-url>` — **destructive**,
  requires `CONFIRM=yes`, runs as the owner role, then re-runs migrations.
- **After every restore, re-apply app-role grants** (backups use
  `--no-privileges`):

  ```bash
  psql "$DATABASE_ADMIN_URL" -v app_role=processtwin_app \
    -v app_password='...' -f scripts/postgres_app_role.sql
  ```

- Rehearse the full cycle (backup → destroy → restore → verify) at least
  quarterly and record measured RTO/RPO. CI runs a roundtrip on every push
  (`postgres` job), but a CI database is not a production drill.

## Secret and credential rotation

| Secret | Effect of rotation | Procedure |
| --- | --- | --- |
| `JWT_SECRET` | All access + refresh tokens invalidated (users re-login) | generate ≥32 chars, roll pods, announce the session reset |
| `METRICS_TOKEN` | Scrapes fail until Prometheus config updated | roll both sides together |
| `DATABASE_URL` / `DATABASE_ADMIN_URL` passwords | brief connection errors during pool recycle | `ALTER ROLE ... PASSWORD`, roll pods |
| TLS certificates | per your ingress/cert-manager policy | external (ROADMAP_EXTERNAL) |

Rotate on your schedule (suggested: quarterly, or immediately on suspected leak).
Store values in your secret manager — never in manifests or `.env` committed files.

## Deploy and rollback

```bash
# Compose
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d   # migrate runs first, then api/web
docker compose -f docker-compose.prod.yml ps      # all healthy?

# Kubernetes
kubectl apply -k k8s/base
kubectl -n processtwin rollout status deploy/processtwin-api
```

- Migrations gate the deploy (`depends_on: service_completed_successfully` /
  init container): if `alembic upgrade head` fails, old pods keep running.
  Kubernetes migration callers acquire a PostgreSQL advisory lock first, so
  concurrent pods do not race schema changes.
- Rollback: redeploy the previous image tag. **Migrations are not automatically
  reversible in production** — the Timescale hypertable conversion is explicitly
  one-way; write a down-migration or restore from backup before schema rollbacks.
- Model releases roll back independently via the registry
  (`PRODUCTION` → `RETIRED` transitions are audited).

## Incident checklist (starter)

1. **Scope:** which alerts fired; `/ready` on affected instances; dashboards.
2. **Correlate:** grab `X-Request-ID`s from failing calls, grep API logs.
3. **Database:** connectivity, pool saturation, replication/lag, disk.
4. **Mitigate:** restart is safe (stateless API); disable bad connectors via
   data-source configuration; roll back a bad deploy/model.
5. **Record:** timeline with request IDs, decision log, follow-up actions.
6. **Post-incident:** update these runbook entries and alert thresholds.

## Health verification after changes

```bash
curl -fsS "$API/live"
curl -fsS "$API/ready"          # expect "database": "ok"
curl -fsS -H "Authorization: Bearer $METRICS_TOKEN" "$API/metrics" | head
bash -n scripts/backup_postgres.sh scripts/restore_postgres.sh   # syntax check
```
