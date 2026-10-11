# ProcessTwin — launch-readiness audit

**Date:** 2026-10-10
**Target:** a startup MVP for demonstrations, early-access users and a controlled, read-only pilot —
not an unvalidated autonomous industrial control system.
**Product proposition under test:** *ProcessTwin helps chemical-process engineers monitor process
data, understand abnormal behaviour, explore operating scenarios, and review explainable operating
recommendations.*

This audit is a software-readiness assessment. It is **not** a plant, safety, cybersecurity,
certification or compliance assessment, and it makes no claim of real-plant validation.

It complements the earlier engineering audit in [AUDIT.md](AUDIT.md); the earlier document remains
the historical record of the Phase-0 baseline and remediation.

## 1. Method

1. Inspected `git status`, `git diff`, the current branch and recent commits.
2. Read `README.md`, `Makefile`, `pyproject.toml`, both Compose files, `.env.example`, the
   migrations, the CI workflow and the existing audit/roadmap documents.
3. Reproduced the required quality gates on this host (Windows, no `make`, no Docker daemon):
   Ruff, strict mypy, pytest, the Next.js production build, the post-build guard, ESLint,
   `tsc` and the frontend unit tests.
4. Started the API with the documented steps, seeded an isolated SQLite demonstration database and
   exercised the core workflows over HTTP with `scripts/smoke_workflows.py`.
5. Scanned tracked files and built frontend artifacts for accidental secrets.

## 2. What is implemented (verified this session)

| Capability | Status | Evidence |
| --- | --- | --- |
| Advisory / read-only boundary | Implemented, tested | `tests/unit/test_read_only_boundary.py`, `tests/unit/test_connector_readonly_boundary.py` pass; no control route in the OpenAPI surface. |
| JWT access/refresh auth, RBAC, tenant scoping | Implemented, tested | `tests/unit/test_authz_matrix.py`, `tests/integration/...` pass; live login + `/auth/me` in the smoke run. |
| Dashboard sourced from the backend, labelled by mode | Implemented, tested | Live `/api/v1/dashboard/summary` returns `source_mode=SIMULATION`; simulator/historical/live are labelled and never mixed. |
| CSTR simulation returns real model results | Implemented, tested | Live run returned a 121-point trajectory; `tests/integration/test_simulation_flow.py` passes. |
| Historical CSV import: preview → map → validate → import → explore | Implemented, tested | Live 3-step run imported 3 rows / 6 measurements then explored them; `tests/integration/test_dataset_upload_flow.py` (12 cases) passes. |
| Recommendation gating + human review | Implemented, tested | `tests/integration/test_dashboard_and_advisory_gate.py` passes; reviews persisted and audited. |
| Read-only OPC UA / MQTT connector adapters + gateway | Implemented, tested (simulator/in-memory) | `tests/unit/test_opcua_connector.py`, `tests/integration/test_opcua_end_to_end.py`, `test_connector_api.py`, `test_gateway_opcua_pipeline.py` pass. |
| Fail-closed production configuration | Implemented, tested | `tests/unit/test_config_hardening.py` passes; placeholders and weak secrets are refused. |
| Production Compose + hardened images + k8s base | Implemented | `docker-compose.prod.yml`, `Dockerfile.api.prod`, `k8s/` present; validated by the CI `images` job. |
| Migrations, RLS, backup/restore | Implemented, CI-verified | `tests/integration/test_postgres_rls.py`, `test_migrations.py` run in the CI `postgres` job against TimescaleDB. |

## 3. Broken or incomplete found in this audit (fixed here)

| Finding | Severity | Root cause | Fix |
| --- | --- | --- | --- |
| `make typecheck` failed: 3 strict-mypy errors | **P0 (gate)** | `redis` ships no type stubs; `np.any(..., axis=1)` is typed `Any`; a stale `# type: ignore` became unused. | Added a `redis` mypy override, bound the TEP result to a typed `NDArray[np.bool_]`, removed the stale ignore. `mypy` now green. |
| CSV import silently dropped uploaded columns that had no tag mapping | **P1 (data integrity)** | The importer only processes mapped tags and never reported the rest. | The import response now returns `unmapped_source_tags` and a `warnings` entry naming every dropped column; the upload UI shows it. Regression test added. |
| No public landing page; `/` redirected straight to `/dashboard` behind the auth gate | **P1 (product)** | The root route was an internal redirect and every non-login path required a session. | `/` is now a public, honest landing page (capabilities, audience, limitations, read-only boundary, demo path). Signed-in visitors are still sent to `/dashboard`. |
| `NEXT_PUBLIC_CONTACT_EMAIL` (optional demo contact) was undocumented | **P2** | New configuration surface. | Documented in `.env.example`; wired through both Compose files as an optional build arg. |

## 4. Incomplete / not implemented (known, out of scope for this pass)

- **OIDC / SSO.** Local JWT only; OIDC is configured but disabled. Acceptable for a pilot,
  not for enterprise identity.
- **Shared (Redis-backed) rate limiting.** The in-process limiter is test-isolated but not
  horizontally shared.
- **Durable connector store-and-forward and certificate/credential rotation.** Adapters are
  read-only and simulator-tested; no long-outage buffering or production PKI operations are proven.
- **Threat model / SBOM / independent penetration test.** Not performed; must not be claimed.
- **Multi-unit-operation modelling.** Only the CSTR reference process is supported, by design.
- **Backup/restore and RLS on this host.** Verified in CI against real TimescaleDB, not on this
  Windows host (no Docker daemon, no PostgreSQL service).

## 5. Unverified on this host (environment limitations — not passes)

| Item | Why unverified here | Where it is verified |
| --- | --- | --- |
| `docker compose build` / production image boot | No Docker daemon reachable on this host (daemon probe times out). | `.github/workflows/ci.yml` `images` job builds every image and smoke-tests `/live`. |
| PostgreSQL migrations, RLS enforcement, backup/restore roundtrip | Requires a PostgreSQL service. | CI `postgres` job. The 9 skipped pytest cases are exactly these infrastructure-gated tests. |
| `make lint` / `make typecheck` / `make test` | `make` is not installed on this host. | The exact underlying commands were run directly (see evidence below); CI runs the `make` targets on Linux. |

## 6. Evidence (this session, Windows host)

```
python -m ruff check apps packages connectors tests   -> All checks passed! (0 errors)
python -m mypy apps packages connectors               -> Success: no issues found in 73 source files
python -m pytest -q                                   -> 204 passed, 9 skipped (see §7 after the new test)
cd apps/web && next build                             -> compiled; 19 routes incl. `/` landing
node scripts/verify-build.mjs                         -> 17 pages OK (manifests present, no duplicate routes)
tsc --noEmit                                          -> clean
next lint --max-warnings 0                            -> no warnings or errors
node --test tests/*.test.ts                           -> 7 passed
python scripts/smoke_workflows.py                     -> 10/10 live workflows passed
```

Live server: `/health` 200, `/live` 200, `/ready` 200
(`database: ok, redis: not_configured`). Route behaviour after the fix: `/` → 200 public landing;
`/dashboard` unauthenticated → 307 `/login?redirect=/dashboard`; `/` with session → 307 `/dashboard`.

Secret scan: no tracked `.env`, no AWS/GitHub/Slack/private-key patterns, no secret-like
assignments in tracked source, nothing matching those patterns in `apps/web/.next`.

## 7. Prioritized plan from these findings

1. **P0 — keep the gate green.** The three mypy errors are fixed; CI must stay the source of truth
   for the DB/container gates this host cannot run.
2. **P1 — finish data-integrity transparency.** Done for unmapped columns; next would be a
   per-version "columns ignored" record surfaced in the dataset detail view.
3. **P1 — product presentation.** Public landing page added. Remaining polish is in
   [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) §Manual configuration.
4. **P1 — staging proof.** Deploy the frontend (Vercel) and API/migrate (compose or k8s) against a
   managed PostgreSQL/Timescale, then run the smoke script against the staging origins.
5. **P2 — identity and operations.** OIDC, shared rate limiting, durable connector buffering,
   threat model, SBOM and an independent security review, before any broader rollout.
6. **Non-software, not substitutable.** HAZOP, plant-data authorisation, a pilot agreement and
   independent security testing remain customer/assessor responsibilities (see
   [ROADMAP_EXTERNAL.md](ROADMAP_EXTERNAL.md)).

## 8. Verdict

See [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) for the per-criterion result and the final verdict.
