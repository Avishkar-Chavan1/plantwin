# ProcessTwin Industrial Readiness Audit

**Assessment scope:** repository code, tests, migrations, deployment files, and documentation inspected on 2026-09-29. Statuses assess implemented behavior—not roadmap intent. `COMPLETE` means complete for the documented demonstrator scope, not certification for safety-critical plant control.

## Executive summary

ProcessTwin already has a modular FastAPI/SQLAlchemy backend, organization membership authentication and RBAC, a SI-based CSTR model, synthetic data simulation, baseline sensor ingestion/quality logic, residual ML, bounded advisory optimization, dashboard, and Docker Compose deployment. Its pre-change CSV endpoint only accepted existing `sensor_id,timestamp,value,unit` readings; no historical dataset lifecycle existed. New work adds tenant-scoped CSV/optional Parquet dataset versions, configurable tag maps and SI units, preserved raw observations, quality reporting, and exploration endpoints. It remains a human-in-the-loop engineering platform: connectors do not control equipment and historical import is not a production historian integration.

## Readiness matrix

| Area | Status | Code evidence and assessment |
| --- | --- | --- |
| Architecture | PARTIAL | `docs/architecture/system.md`, `apps/api/processtwin_api/main.py`, `packages/*`, and `apps/*` separate API, physics, ML, optimization, simulator, and connectors; `Base.metadata.create_all` runs only when `Settings.auto_create_schema` allows it (development/test); production schema changes are exclusively the migration job's, and compose/k8s gate deploys on `alembic upgrade head`. |
| Database | PARTIAL | SQLAlchemy models in `apps/api/processtwin_api/models.py`, Alembic `migrations/versions/0001_initial_schema.py`, SQLite and PostgreSQL/Timescale Compose config exist. New dataset entities use tenant IDs. Database RLS exists (migration `0006_security_rls_refresh_tokens`) and is proven in CI against a non-owner app role; a real production backup/restore rehearsal with measured RTO/RPO remains external. |
| Authentication | PARTIAL | JWT login/refresh and bcrypt verification in `apps/api/processtwin_api/auth.py`; secret defaults are intentionally development-only, server-side refresh-token rotation/revocation is implemented, and OIDC remains an external-IdP integration point (`OIDCProvider` protocol placeholder). |
| RBAC | PARTIAL | `RoleName`, `tenant_context`, `require_roles` and engineer/admin dependencies enforce route-level roles. Coverage is an executable route-by-route matrix (`tests/unit/test_authz_matrix.py`) asserting public/user/tenant/role-gated status and the exact role set for every route, and requiring every mutation to be role-gated. |
| Tenant isolation | PARTIAL | Organization membership gates requests; queries for plants, sensors, datasets, and observations filter organization IDs. Database RLS policies cover 28 tenant tables plus memberships/refresh tokens; enforcement requires the non-owner app role (`scripts/postgres_app_role.sql`, two-role `DATABASE_URL`/`DATABASE_ADMIN_URL` posture) and is proven by the CI `postgres` job. |
| Physics | PARTIAL | `packages/physics/cstr.py` implements the SI liquid CSTR mass/energy balances and parallel Arrhenius reactions; pressure is explicit Pa context but is not used by this incompressible law. `packages/calibration/cstr.py` calibrates/evaluates against GOOD historical observations. No plant dataset was supplied, so no plant model is validated. |
| Simulator | COMPLETE (synthetic demo only) | `apps/simulator/processtwin_simulator/plant.py`, `run.py`, and `seed_demo.py` produce expressly simulated data and alerts. Not a real plant connector. |
| Data quality | PARTIAL | `apps/api/processtwin_api/quality.py` handles limits, finite values, order, duplicates and basic stuck/spike cases. New `packages/data_ingestion/pipeline.py` preserves row/value and applies missing, duplicate, order, limits, stuck, spike, rate, gap, timestamp, unit and drift rules to imported history. Heuristics/limits need site engineering review; baseline rejected REST bad measurements are still recorded as quality events rather than retained as readings. |
| ML | PARTIAL | `packages/ml/pipeline.py` trains chronological direct-ML and residual learners with computed train/validation/test metrics. The legacy worker path still consumes demo sensor tags; the new import-based API requires canonical mapped observations. No real-site dataset is present. |
| Hybrid model | PARTIAL | Legacy worker retains synthetic residual training. `POST /api/v1/models/hybrid/train` compares physics-only, ML-only and physics+residual predictions on imported measured product-B data with ordered splits. It registers VALIDATION only; no independent industrial performance claim is made. |
| Calibration and evaluation | PARTIAL | Versioned parameter records and `CalibrationRun` store bounds, before/after values, dataset/user/code provenance and train/validation/test physics scores. Physics-only evaluation requires an independent dataset version and reports measured/predicted/residuals, MAE/RMSE/R²/MAPE/bias. Automated fixtures are synthetic; no plant calibration/evaluation occurred. |
| Model validity envelope | PARTIAL | Model versions can store explicit SI or engineering-unit-keyed envelopes; independent evaluation refuses unconfigured/out-of-range predictions and emits `OUTSIDE_VALIDATED_MODEL_RANGE`. Site-specific limits and evidence-based validation are not configured in this repository. |
| Model registry / lifecycle | PARTIAL | SQL `ModelVersion` tracks dataset/parameter IDs, schemas, windows, metrics, hyperparameters, envelope, SHA, creator and time. State flow distinguishes CALIBRATED, EVALUATED, VALIDATED, STAGING, PRODUCTION, RETIRED; human validation and admin transitions are required. Optional MLflow mirroring is metadata/metrics only; SQL is authoritative. |
| Model drift | PARTIAL | `packages/ml/drift.py` reports PSI, KS and Jensen–Shannon metrics for stored feature/target/physics and available hybrid residual references. Events are persisted as `MODEL_DRIFT_DETECTED`; there is no automatic retraining. Thresholds need site-specific governance and real reference windows. |
| Optimization | PARTIAL | `packages/optimization/service.py` bounds an advisory search against model constraints and API persistence; plant envelopes and human approval workflow require real site governance. |
| Dashboard | PARTIAL | `apps/web/components/historical-data-explorer.tsx` provides hierarchy selection, upload/mapping, SI summaries, sampled trends, gaps and quality display; model governance screens exist (`model-governance.tsx`: registry lifecycle, independent evaluations, drift events, calibration history, role-gated validate/stage/promote with acceptance limits); calibration metric plotting and envelope editing remain. |
| MQTT | PARTIAL | `connectors/mqtt/adapter.py` validates topic and JSON reading shape; broker lifecycle, delivery guarantees and operational hardening are absent. |
| OPC-UA | PARTIAL | `connectors/opcua/adapter.py` is explicitly read-only/disabled without configuration; no validated production server integration. |
| Observability | PARTIAL | API exposes health and Prometheus request/latency/ingestion metrics in `apps/api/processtwin_api/main.py`; readiness covers database/Redis, Prometheus alert rules ship in `observability/prometheus-rules.yml`, and `docs/OPERATIONS.md` is the starter runbook; distributed tracing and actual alert delivery remain deployment-specific. |
| Security | PARTIAL | `apps/api/processtwin_api/auth.py`, `audit.py`, API tenant queries and upload limits provide a baseline; rate limiting (including login-specific limits), secrets validation, token revocation and a blocking CI dependency audit (pip-audit + Dependabot) are implemented; external identity, managed secrets, scheduled key rotation and independent penetration testing remain deployment work. |
| Testing | PARTIAL | Tests now cover synthetic CSTR calibration recovery/bounds, irregular timestamp handling, evaluation residual metrics, chronological ML comparator scores, drift signals, validation gates and immutable production lifecycle in addition to data-import regression. No test is industrial evidence or a safety certification. |
| Deployment | PARTIAL | `Dockerfile.api`, app Dockerfiles, `docker-compose.yml`, `docker-compose.prod.yml`, Makefile, and `.github/workflows/ci.yml` provide a container demo and CI. CI builds images, smoke-tests the production API entry point, audits dependencies, and rehearses migrations/RLS/backup-restore against real TimescaleDB; running an actual production deployment and recovery exercise remains. |
| REST/database sources | MISSING (transport) | New `connectors/historical.py` defines clear future read-only source contracts; REST/database transport, credential management and query governance are intentionally not implemented. |
| Historical import | PARTIAL | New authenticated `/api/v1/datasets/import` persists versioned CSV/Parquet observations and provenance; upload is bounded to 5 MB by default and 100,000 rows. No background/chunked import for larger historian exports or object-store artifact retention yet. |
| Data exploration | PARTIAL | New variable, summary statistics, percentiles, correlation, sampled trend, quality, outlier and gap APIs are tenant-scoped. Frontend exploration workflow and pagination for long series remain. |

## Data foundations added in this work

- Added SQLAlchemy entities `DataSource`, `Dataset`, `DatasetVersion`, `PlantTag`, `TagMapping`, and `DatasetObservation`; the existing `Plant`, `ProcessUnit`, and `Equipment` hierarchy remains in use.
- Added explicit source-to-canonical mapping: arbitrary historian tags never need to be hard-coded in code. Mappings can include hierarchy, engineering bounds, expected interval, rate, and drift limits.
- Added CSV ingestion using the standard library and optional Parquet ingestion using the `parquet` extra (`pyarrow`). REST/database interfaces are future read-only contracts, not live integrations.
- Normalizes temperature to K, pressure to Pa, mass/volume flow to kg/s or m³/s, concentration to mol/m³, power to W, energy to J, and heat capacity to J/(kg·K). Existing aliases include °C, bar, kPa, kg/h, L/min, kW, and kJ/kg-K.
- Persists historical original value/text/unit/timestamp alongside normalized value/unit, quality status/reasons, row number, file checksum, version and aggregate report. Bad/suspect/missing observations are retained and not silently discarded.
- New APIs: `GET /api/v1/datasets`, `POST /api/v1/datasets/import`, `GET /api/v1/datasets/{version_id}/variables`, `GET /api/v1/datasets/{version_id}/exploration`. Requires bearer authentication, `X-Organization-ID`, and OWNER/ADMIN/ENGINEER role. Upload form includes `plant_id`, `dataset_name`, JSON `mappings`, optional `timestamp_column`, `description`, and `dataset_id` for another version.
- Follow [Historical data import and exploration](data-import.md) for examples and workflow details.

## Model calibration phase (2026-09-29)

### COMPLETE in code (not equivalent to industrial validation)

- Audited the existing CSTR balances; preserved its physics rather than introducing fitted equations. Hardened finite SI checks and added exact, irregular historian timestamp integration.
- Added bounded parameter metadata/version persistence and append-only calibrated parameter-set versions; the fitter rejects simultaneously fitting `U` and `A` because only `UA` is observable here.
- Added history-backed, GOOD-observation alignment for required CSTR input/output canonical tags; calibration records exact dataset, user, parameter set, optimizer, bounds, objective, metrics, code SHA and chronological windows.
- Added physics-only predictions/residuals and computed MAE, RMSE, R², MAPE, bias and residual quantiles; independent dataset evaluation and out-of-envelope suppression are implemented.
- Improved ML comparator evaluation to compute physics-only, direct ML-only, and physics-plus-residual metrics for train, validation and held-out test windows without shuffling.
- Added explicit VALIDATED → STAGING → PRODUCTION transitions with human acceptance checks, SQL model-version provenance, optional MLflow metadata/metric logging, and persisted PSI/KS/Jensen–Shannon drift reports. Drift never triggers retraining or edits a production model.
- Added migration `0003_calibration_registry` for existing historical-data schema deployments.

### IN PROGRESS

- The framework is implemented, but all calibration examples/tests use deterministic synthetic trajectories. No user-provided industrial historical dataset was present; no live model has a real measured calibration or independent plant evaluation.
- SQL is authoritative and optional MLflow mirroring is supported when the `mlflow` extra and `MLFLOW_TRACKING_URI` are supplied. Artifact store governance, registry synchronization/reconciliation and production deployment operations are not fully validated.

### REMAINING

- Obtain an authorized real dataset mapped to the CSTR canonical inputs and at least one measured response; have process engineers confirm tag units, historian alignment, sensor quality, initial-state assumptions, parameter bounds/identifiability, and envelope limits.
- Review measured-vs-predicted residuals by operating regime and time, select independent holdout periods, and sign explicit validation acceptance limits. Until then all model versions remain unvalidated for the plant.
- Extend multi-reactor/process response models and calibrate additional targets only when measurements make parameters identifiable; add persistent MLflow artifact logging and test remote server lifecycle.
- Add frontend calibration review, metrics/residual plots, human validation, envelope editor, and drift-event review workflows. No automatic retraining.
- Add drift reference/window policy and false-alarm/seasonality review from real operations; current thresholds are configurable statistical screening values, not approved plant alarms.
- Continue historical import hardening for larger datasets, object storage, and site-reviewed quality thresholds.

## COMPLETE / IN PROGRESS / REMAINING / TEST STATUS

- **COMPLETE (implementation scope):** SI CSTR calibration/evaluation framework, append-only parameter versions, independent evaluation API, explicit envelope checks, chronological physics/ML/hybrid metrics, governed status transitions, drift statistics/persistence, docs and targeted regression tests.
- **IN PROGRESS:** readiness for customer-supplied real historical observations, independent plant assessment, operational registry/artifact management, and human review UI.
- **REMAINING:** real industrial dataset acquisition and mapping, process-engineer review/signoff, real data validation, site operating envelope, MLflow production artifact governance, operator-ready evaluation/drift dashboard.
- **TEST STATUS:** `123 passed, 5 skipped` locally (verified 2026-10-04; the 5 skips are the PostgreSQL-only RLS proofs, which run in the CI `postgres` job); `ruff`, strict `mypy`, and `tsc --noEmit` report zero errors. The local Windows Next.js build compiles and type-checks but cannot copy standalone traced symlinks; the full image build runs in Linux CI. Passing gates do not establish industrial validation: calibration fixtures remain synthetic and no plant dataset has been reviewed.

See [Model calibration, evaluation and monitoring](model-calibration.md) for mapping requirements, equations, API payload scope and the validation boundary.

## What was already present

- Initial organization/user/membership schema, plants, process units, equipment, sensors, readings, quality events, audit history, and tenant-scoped API access.
- JWT authentication and five roles; CSTR SI physics, synthetic CSTR generator, REST reading and row-oriented CSV ingestion, bounded optimization, residual ML, MQTT/OPC-UA adapters, Next.js demo dashboard, Prometheus endpoint, Docker Compose, Alembic baseline, and CI workflow.
- Existing unit conversion already covered core temperature, pressure, flow, concentration, power and heat-capacity units; this work exposes canonical SI units and uses them in stored history rather than replacing that utility.

## Not represented as real industrial data

The included reference histories, default dashboard state, seeded labels and example sensor values are synthetic. No industrial process dataset is included. A user-imported dataset is treated as user-supplied historical data, but the application cannot verify its source, calibration, completeness, operating context, or regulatory chain of custody. Neither the synthetic demo nor an import constitutes process-model validation.

## Remaining work

1. Exercise revisions `0002_historical_datasets` and `0003_calibration_registry` against a representative upgraded PostgreSQL/Timescale production backup; local clean-SQLite upgrade succeeded, but a deployed database snapshot was not available.
2. Configure/install the optional `parquet` extra and run Parquet integration tests in CI; test for a missing extra returns a clear supported-format dependency error.
3. Add version mapping edits as immutable versioned records, checksummed object storage, chunked/background imports, idempotent retries and database-side tenant constraints.
4. Add chart-based historical exploration and frontend calibration/evaluation/validation/drift-review screens.
5. Add historian/REST/database read-only connectors, sampling-aware tests and production connector observability.
6. Independently review quality and drift thresholds with process engineers; establish alarms, response procedures, backup/recovery, threat review and deployment operations.
7. Expand authorization and migration upgrade tests; strict mypy and the web type check are now clean (`tsc --noEmit` passes; `next build` completes except for standalone file tracing, which cannot create symlinks on this Windows host and runs in CI on Linux).

## Validation record

- Python `pytest`: `113 passed, 0 skipped` (92.67s, 2026-10-03). The optional Parquet tests run when the `parquet` extra is installed and skip cleanly when it is not. Two existing deprecation warnings (Starlette/httpx and NumPy/joblib) remain.
- Ruff (`ruff check apps packages connectors tests`): passed (0 errors).
- Alembic: `upgrade head` and `current` succeeded against fresh SQLite; current revision is `0003_calibration_registry`, plus the uncommitted Timescale hypertable/retention revision.
- Strict mypy (`mypy apps packages connectors`): **0 errors in 68 files** (was 105 in 7 at the Phase 0 audit). Verified both with and without the optional `pyarrow` dependency installed.
- Frontend checks: `tsc --noEmit` passes and `next build` produces the standalone output locally (verified 2026-10-04 with pnpm 10).

## Hardening phase (2026-10-04)

Evidence added beyond the baseline remediation:

- **Authorization contract:** `tests/unit/test_authz_matrix.py` classifies all 54 API routes as public / user / tenant / role-gated with exact role sets (governance transitions are OWNER/ADMIN only). Any new, unclassified route or ungated mutation fails the suite.
- **Vulnerability gate:** CI `dependency-audit` runs pip-audit as a blocking job; pytest was upgraded to `>=9.0.3` to clear PYSEC-2026-1845; `.github/dependabot.yml` proposes weekly pip/npm/actions/docker updates.
- **PostgreSQL CI proof (`postgres` job):** migrations against real TimescaleDB (the hypertable/compression/retention revision actually executes), app-role provisioning via `scripts/postgres_app_role.sql`, RLS enforcement tests (`tests/integration/test_postgres_rls.py`: 30 policies present, unscoped invisibility, tenant/principal scoping, plus an end-to-end login → tenant read → cross-tenant denial → refresh rotation → logout flow *while RLS is enforced*), and a backup/restore roundtrip with the production scripts including the mandatory post-restore grant re-application.
- **Two-role production posture:** compose and k8s separate `DATABASE_ADMIN_URL` (owner, migration job only) from `DATABASE_URL` (non-owner app role). Postgres table owners bypass RLS, so single-role deployments silently disabled database-level tenant isolation — the largest latent gap closed in this phase.
- **Release correctness:** the migration runner holds a PostgreSQL advisory lock, so Kubernetes pod init containers cannot race schema upgrades during a rollout or scale-out. The non-owner app role is provisioned before the first migration so default grants cover owner-created tables.
- **Web configuration correctness:** `NEXT_PUBLIC_API_URL` is now a mandatory image build argument, rather than a runtime-only container variable that Next.js had already inlined into browser JavaScript.
- **Operations:** `observability/prometheus-rules.yml` (alerts derived from metrics the API actually exposes) and `docs/OPERATIONS.md` (probes, log correlation, backup cadence, secret rotation, deploy/rollback, incident checklist).
- **Model governance UI:** `apps/web/components/model-governance.tsx` wired into the `models` view — registry with lifecycle status, independent evaluations, drift events, calibration history, and role-gated validate (acceptance limits + signed review note) / stage / promote actions.

Still open in this area: OIDC integration, alert delivery/on-call assignment, a production backup rehearsal with measured RTO/RPO, load testing, and image signing — tracked in `docs/ROADMAP_EXTERNAL.md`.
