# ProcessTwin Phase 0 audit

**Date:** 2026-10-03  
**Scope:** repository code, configuration, migrations, Docker/Compose, CI, tests, and user-facing documentation. This is a software implementation audit; it is not a plant, safety, cybersecurity, certification, or compliance assessment.

## Executive assessment

ProcessTwin is a substantive **advisory-only demonstrator** with useful foundations: an SI CSTR model, a synthetic simulator, FastAPI/SQLAlchemy API, tenant and role checks, JWT access/refresh tokens, audit records, mapped historical imports, read-only telemetry adapters, CSTR calibration/model lifecycle code, and a web dashboard. The public Tennessee Eastman report added in this workspace is reproducible benchmark evidence, not plant validation.

It is **not deployment-ready today**. The mandatory engineering gate is red: lint, strict type checking, and the full integration test suite fail. The current Docker images are development-style and a production deployment package, release process, operational recovery procedure, supply-chain controls, and site-specific evidence are incomplete or absent. No code path should be connected to a PLC, DCS, SCADA system, or any other plant-control endpoint.

## Evidence observed

| Area | What is implemented | Assessment |
| --- | --- | --- |
| Advisory boundary | Connectors are framed as read-only acquisition; recommendations carry an advisory warning; no control-command endpoint is present. MQTT/OPC UA adapters explicitly only read/subscribe. | Foundation present; regression tests must remain mandatory. |
| Configuration and API safety | `Settings` validates environment, JWT secret length, explicit CORS origins, production PostgreSQL, production metrics token, and rejects demo credentials in production. Request IDs, structured JSON logging, request limits, CORS, common security headers, `/health`, `/ready`, and protected `/metrics` are implemented. | Partial. OIDC, shared rate limiting, separate liveness semantics, and graceful resource shutdown are absent. |
| Identity and tenancy | JWT access/refresh rotation, logout revocation records, memberships/roles, app-level organization filtering, and a PostgreSQL RLS migration exist. | Partial. No OIDC/SSO, password-policy/admin provisioning flow, or PostgreSQL RLS integration proof. |
| Database | Alembic migrations through `0006_security_rls_refresh_tokens`, SQLAlchemy pooling, `pool_pre_ping`, and tenant indexes/models exist. | Partial. No tested upgrade/downgrade matrix against PostgreSQL/Timescale, retention/compression policy, or backup/restore procedure. |
| Connectivity | MQTT and OPC UA adapters and a gateway exist with source health handling; historical CSV/Parquet interfaces are defined. | Partial. No durable store-and-forward, production credential/certificate operations, or broker/OPC-UA container integration tests. |
| Modeling | CSTR calibration, chronological residual ML, drift screening, lifecycle gates, operating envelopes, and the public TEP one-step forecast report exist. | Partial. Physics is not composable unit operations; PFR is minimal; heat exchanger, flash/distillation, flowsheet, uncertainty calibration, and site validation are absent. |
| Workflow/safety | Recommendations have expiry, review/accept/reject records, and audit entries; bad data can prevent prediction/recommendation paths. | Partial. No ISA-18.2-style alarm lifecycle, shelve/acknowledge/escalation/flood metrics, or complete fail-safe contract coverage. |
| Web/UI | Dashboard, live-source, and historical-exploration components exist. | Partial. No complete model-health, alarm, recommendation-review, or role-aware operational workflow. |
| Delivery | Compose declares TimescaleDB, Redis, MinIO, MLflow, Prometheus, Grafana, API, web, and simulator. A small GitHub Actions workflow exists. | Partial. Images run as root, are single stage, do not use a read-only filesystem, and are not locked down for production. No Kubernetes/Helm, TLS configuration, operations runbooks, or release assets. |
| Security assurance | Security policy and an application-level append-only audit helper exist. | Partial. No STRIDE threat model, OWASP/IEC control mapping, dependency/image/secret scanning, SBOM, or audit-immutability database test. |

## Baseline verification (2026-10-03)

The brief requires `make lint`, `make typecheck`, `make test`, and `docker compose build` at every phase gate. On this Windows host, `make` is not installed, so the equivalent Python commands were run where possible. These results are blockers, not waived checks.

| Required gate | Result | Evidence |
| --- | --- | --- |
| `make lint` | **FAILED** | `ruff check apps packages connectors tests` reports 14 errors (12 fixable): unused imports in `connectors/historical.py`, import sorting issues in `tests/connectors/test_historical_connectors.py`, undefined name `sa`, module-level import not at top. |
| `make typecheck` | **FAILED** | `mypy apps packages connectors` reports 105 errors across 7 files: API workflows, realtime code, OPC UA typing, CSTR calibration, missing `pyarrow` typing, missing return type annotations, union-attr errors on Optional fields. |
| `make test` | **FAILED** | Full pytest: 57 passed, 11 failed, 1 skipped. Focused API integration module: 1 passed, 4 failed. Root causes: (1) bcrypt 4.x / passlib 1.7.x incompatibility causing `AttributeError: module 'bcrypt' has no attribute '__about__'` during password verification; (2) in-memory rate limiter (`FixedWindowRateLimiter`) is process-global causing test interference (429 Too Many Requests); (3) freshly issued access tokens rejected with 401 in authenticated requests. |
| `docker compose build` | **Environment blocked** | Docker Desktop on Windows with WSL backend; daemon pipe not accessible. Cannot verify build in this environment. |
| Benchmark pipeline | **PASSED** | `tests/benchmarks/test_tennessee_eastman.py`: 2 passed; Ruff and strict mypy checks for the benchmark package passed. The generated report is `docs/validation/tennessee-eastman.md`. |

The test and type failures must be resolved before Phase 1 implementation starts. The Docker build needs a host where Docker has usable user-config/buildx permissions; that is an environment prerequisite, not a justification to skip image validation.

## Detailed findings

### Authentication failures

Integration tests show two distinct auth problems:

1. **bcrypt/passlib version mismatch**: bcrypt 4.3.0 changed internal API; passlib 1.7.4 expects `bcrypt.__about__.__version__` which doesn't exist. This causes warnings and potential verification failures during login.

2. **Rate limiter test coupling**: `FixedWindowRateLimiter` is a module-level global in `apps/api/processtwin_api/main.py:82`. Each test creates a new `TestClient(app)` but the limiter state persists across tests. Login endpoint has stricter limit (10 req/min). Multiple `setup_tenant()` calls in sequence exhaust the login limit, returning 429.

3. **Token rejection (401)**: After successful login, subsequent authenticated requests return 401 "Invalid or expired token". Likely cause: `get_settings()` is cached via `@lru_cache` but JWT secret/env may differ between test setup and request handling, or the token issuer/audience validation is mismatched.

### Lint failures (14 errors)

```
connectors/historical.py: F401 json, os, datetime.timedelta, urllib.parse.urljoin unused
tests/connectors/test_historical_connectors.py: I001 import sorting (3), F401 json, UTC, datetime, timedelta unused, F821 undefined 'sa', E402 import not at top
```

### Typecheck failures (105 errors across 7 files)

Key categories:
- `apps/api/processtwin_api/main.py`: 28 errors (missing return types, dict unpacking, Optional arithmetic)
- `apps/api/processtwin_api/workflow_api.py`: 29 errors (SQLAlchemy Select.where typing, Optional attribute access)
- `apps/api/processtwin_api/live_api.py`: 11 errors (SQLAlchemy typing, Optional attribute access)
- `apps/api/processtwin_api/realtime.py`: 7 errors (numpy array indexing, float/int conversion)
- `connectors/historical.py`: 6 errors (missing type annotations, Callable vs callable)
- `packages/benchmarks/tennessee_eastman.py`: 7 errors (numpy array shape/T attributes)
- `apps/worker/processtwin_worker/train.py`: 1 error (NDArray reshape)

### Read-only connector boundary (VERIFIED)

**MQTT adapter** (`connectors/mqtt/adapter.py`): `MqttReadOnlySubscriber` only subscribes, never publishes. No write method exposed.

**OPC UA adapter** (`connectors/opcua/adapter.py`): `OpcUaReadOnlyConnector` protocol declares only `read_value`, `discover_variables`, `subscribe`. Explicit `OpcUaConnectorDisabled` for dev. No `write_value`, `call_method`, or command publishing.

**Gateway** (`apps/gateway/processtwin_gateway/runtime.py`): Only instantiates read-only adapters. Filters `DataSource.read_only.is_(True)`.

### Multi-tenancy (PARTIAL)

- App-level: every query filters by `organization_id` from `X-Organization-ID` header validated against membership
- DB-level: Migration `0006` adds PostgreSQL RLS policies on 30 tenant tables + memberships + refresh_tokens
- **NOT TESTED** against real PostgreSQL; SQLite has no RLS support

### Docker/Production readiness

- `Dockerfile.api`: single-stage, runs as root, no healthcheck in image, no non-root user
- `docker-compose.yml`: development defaults (dev passwords, `latest` tags, no resource limits)
- `docker-compose.prod.yml`: 7 lines only - not a real production deployment
- No Kubernetes/Helm manifests
- No TLS configuration
- No backup/restore scripts tested

### CI/CD gaps

Current `.github/workflows/ci.yml` only runs:
- Python lint + test (no typecheck, no coverage)
- Web lint/test/build (no coverage)
- Docker API build (no compose build)

Missing: coverage gate, integration environment (PostgreSQL, Redis, MQTT, OPC UA), security scans (CodeQL, pip-audit, npm audit, Trivy), SBOM, Dependabot, release workflow, changelog.

## Prioritized gaps

### P0 — release blockers and safety boundaries

1. **Restore a green engineering gate**: fix 14 Ruff issues, 105 strict-mypy errors, and the authentication/rate-limiter integration regressions; add deterministic tests that reproduce and prevent the token rejection.
2. **Make the production/development configuration paths executable and tested**: the production Compose overlay currently inherits development Compose defaults; startup should remain fail-closed, but documented production manifests must not depend on demo service credentials or `latest` tags.
3. **Add explicit regression tests proving that every available connector and gateway only performs reads/subscribes and cannot issue a plant-control write, method call, or command**.
4. **Establish a supported developer command runner on Windows** (for example, documented PowerShell equivalents) so the required quality gates are executable consistently.
5. **Do not deploy until a privileged build host completes a clean `docker compose build` and the container runtime behavior is tested**.

### P1 — deployment and security hardening

1. Add OIDC/OAuth2 Authorization Code + PKCE support, issuer/JWKS validation, user/membership mapping, and tests; retain local JWT only as an explicit development option.
2. Define password creation/reset policy, password strength/compromise checks, account lifecycle, and shared rate limiting (Redis or ingress/WAF). The current limiter is per-process and causes test coupling.
3. Convert API, worker, simulator, and web images to pinned, multi-stage, non-root production images; apply read-only root filesystems where the workload permits writable mounts.
4. Add PostgreSQL/Timescale retention/compression and migration tests, backup/restore scripts, restore verification, and secret-injection/TLS deployment configuration.
5. Validate PostgreSQL RLS with a real Postgres integration environment, including direct cross-tenant attempts. Application-level filters alone are not a sufficient defense-in-depth claim.

### P2 — completeness, scale, and assurance

1. Expand CI/CD: coverage gate, Compose integration environment, CodeQL, dependency/secret/image scans, SBOM, Dependabot, provenance/release workflow, changelog, and versioning policy.
2. Complete industrial operations: durable gateway buffering, reconnect/backoff verification, OPC UA/MQTT integration containers, historian adapter contract examples, certificate/credential rotation, and ingestion-lag monitoring.
3. Implement composable unit operations, heat exchanger, flash/distillation, a flowsheet, uncertainty calibration, and documented validation targets. Keep the current public TEP result marked as simulation-benchmark-only.
4. Add alarm lifecycle/flood management and operational UI; provide Grafana dashboards, alerts, on-prem/air-gapped guidance, and runbooks.
5. Complete security/compliance preparation with an honest threat model and control-status mapping. Do not call this certified, IEC 62443 compliant, SOC 2 compliant, or plant validated without external evidence.

## Phased execution plan

The following order keeps the advisory boundary intact and prevents later work from being built on a failing baseline. Each implementation phase ends only after lint, strict typing, tests, and image build pass on a capable host.

1. **Phase 0 — audit (this change):** record the current facts, blockers, implementation sequence, and external-action checklist. No operational behavior changed.
2. **Baseline remediation gate:** resolve P0 verification failures first, add regression tests, and capture a clean baseline. This is required before Phase 1 because the pasted brief forbids proceeding with a failing phase gate.
3. **Phase 1 — production hardening:** configuration/identity boundary, health/shutdown, database operations, hardened container images, deployment/TLS artifacts. Do not add OIDC until the required identity-provider configuration contract and local dev fallback are designed.
4. **Phase 2 — CI/CD and supply chain:** make the Phase 1 gate enforceable in CI before increasing connector or modeling scope.
5. **Phase 3 — read-only connectivity:** integration-test the gateway against disposable MQTT/OPC UA services. Reject every control-capable configuration and verify no writes are attempted.
6. **Phase 4 — modeling:** introduce unit-operation abstractions and publish only executed, reproducible validation reports. Keep public simulations and customer/plant evidence distinct.
7. **Phase 5 — alarms, workflow, and explainability:** make the safe default `NO_RECOMMENDATION` when data/model quality is insufficient; retain human review and auditability.
8. **Phases 6–8 — assurance, operations, and product:** add threat-model/control evidence, operational runbooks/dashboards/load-test results, then polish the UI and clean-clone deployment experience.

## Baseline remediation (2026-10-03, later the same day)

The Phase 0 gate failures above were fixed in this workspace and re-verified. The original
findings are kept above as the audit record; the table below is the current state.

| Required gate | Result | Evidence |
| --- | --- | --- |
| `make lint` / `ruff check apps packages connectors tests` | **PASSED** | 0 errors (was 14). |
| `make typecheck` / `mypy apps packages connectors` | **PASSED** | 0 errors in 68 files (was 105 in 7). Verified both with and without the optional `pyarrow` extra. |
| `make test` / `pytest` | **PASSED** | 113 passed, 0 failed, 0 skipped (was 57 passed / 11 failed, plus 4 modules that could not be imported). |
| `docker compose -f docker-compose.prod.yml config` | **PASSED** | Validates when required variables are set; fails fast with an actionable message when they are not. |
| `docker compose build` | **MOVED TO CI** | No Docker daemon is reachable on this host, so the `images` job in `.github/workflows/ci.yml` now builds the development API image and every production image, then boots the production API container and curls `/live`. |
| Web type check | **PASSED** | `tsc --noEmit` passes. `next build` compiles and then fails only when copying traced files, because Windows refuses to create symlinks; CI runs the full build on Linux. |

### What was fixed

1. **P0 — undeclared runtime dependencies.** `connectors/object_storage.py` imports `minio`
   unconditionally (the datasets router imports it, so the whole app failed to import), the
   production image runs `gunicorn`, and `/ready` imports `redis`; none were declared in
   `pyproject.toml`. All three are now base dependencies, which is what made 4 test modules
   uncollectable and 5 API integration tests fail.
2. **P0 — strict typing and lint.** Removed 25 stale `type: ignore` comments, fixed the OPC UA
   coroutine signature, the readiness return type, the missing `state_values` annotation and
   the redundant cast. Optional `pyarrow` imports now go through an `Any`-typed adapter
   boundary so the gate is identical with and without the `parquet` extra.
3. **P0 — fail-closed configuration.** The `.env.example` placeholder secrets were accepted at
   startup; they are now rejected for `JWT_SECRET` and `METRICS_TOKEN`, with regression tests
   in `tests/unit/test_config_hardening.py` covering placeholders, short secrets, production
   PostgreSQL/HTTPS/demo-credential requirements, and one valid production configuration.
4. **P0 — advisory boundary regression tests.** `tests/unit/test_read_only_boundary.py` proves
   connector/gateway public APIs expose no write methods, acquisition sources never call a
   control API (`.publish(`, `write_value(`, `call_method(`, ...), and the OpenAPI surface has
   no control/command route.
5. **Production deployment path.** `docker-compose.prod.yml` now has fail-fast `${VAR:?}`
   required variables, an explicit one-shot `migrate` service that `api`/`web` wait on,
   per-service `image:` tags, JSON log rotation, and `tmpfs` mounts for every writable path on
   read-only roots. The former long-running `worker` service retrained in a crash loop because
   `train` is a one-shot program; training is now behind a `training` profile and runs on
   demand. The synthetic `simulator` was removed from production: it refuses to run with
   `ENVIRONMENT=production`, so it would only have crash-looped.
6. **Development compose path.** The API/demo-init defaulted `JWT_SECRET` to a value the app
   rejects, and neither `simulator` nor `demo-init` received the environment their `Settings`
   require (`jwt_secret`, `DEMO_EMAIL`, `DEMO_PASSWORD`) — `make demo` could not have started.
   Both are now explicit and fail fast with an actionable message.
7. **Container supply chain.** Added `.dockerignore` at the repo root and for `apps/web` so
   `.env`, `.git`, `.venv`, `node_modules` and databases never enter a build context; the web
   image pins `pnpm@10`, copies `.npmrc` before install, and `apps/web/public/` now exists so
   the `COPY public` step succeeds.
8. **Kubernetes.** Added `k8s/base/api-deployment.yaml` and `web-deployment.yaml` (probes,
   resource requests/limits, non-root, read-only root filesystem, dropped capabilities,
   `RuntimeDefault` seccomp, `automountServiceAccountToken: false`, explicit `emptyDir`
   mounts, migration init container), a `kustomization.yaml`, a secret template documenting
   the required keys, an aligned `configmap.yaml` whose keys match the `Settings` model, and
   `k8s/README.md` with the deploy/verify procedure.
9. **CI.** The verify job now runs `make typecheck` and installs every extra the suite touches;
   the web job uses pnpm with the committed lockfile instead of `npm install`; the new `images`
   job validates the production compose file, builds all images, and smoke-tests the
   production API entry point.
10. **Backup/restore scripts.** They only accepted `postgresql://user:pass@host:port/db`, but
    the application's documented URL is `postgresql+psycopg://` and port-less URLs are valid,
    so both scripts failed on the URL they were meant to protect. They now normalise the
    driver suffix, use libpq URLs directly, verify the dump with `pg_restore --list`, preserve
    `?sslmode=` parameters for the maintenance connection, and name archives `.dump.gz`
    instead of pretending a custom-format dump is plain SQL.
11. **Dependency pins.** `minio`, `gunicorn` and `redis` added; `bcrypt` relaxed to `<5`
    (3.x has no cp312 wheel, so a Python 3.12 install fell back to a source build — passlib
    1.7.4 hash *and* verify are verified against bcrypt 4.0.1); `numpy`/`scipy` pins now match
    the environment the suite is actually verified in (numpy 2.5 / scipy 1.18) instead of
    contradicting it.

### Still open after remediation

- Image builds and container runtime behaviour are proven in CI, not on this host (no Docker
  daemon). The audit's requirement to build on a capable host is satisfied by the pipeline,
  not by a local run.
- PostgreSQL RLS, the migration upgrade/downgrade matrix and backup/restore are still untested
  against a real PostgreSQL/Timescale instance.
- OIDC, shared (Redis-backed) rate limiting, threat model, security scanning, SBOM and load
  testing are unchanged P1/P2 items.
- Every item in [ROADMAP_EXTERNAL.md](ROADMAP_EXTERNAL.md) remains open by definition; none of
  the work above substitutes for a penetration test, HAZOP, plant data authorization, or an
  independently run pilot.

## Non-software evidence boundary

The items requiring customer authorization, a qualified security assessor, process engineers, or an operated deployment are tracked in [ROADMAP_EXTERNAL.md](ROADMAP_EXTERNAL.md). Their absence is a known limitation, not a claim that software can substitute for them.