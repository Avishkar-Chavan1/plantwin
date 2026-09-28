# ProcessTwin Industrial Readiness Audit

**Assessment scope:** repository code, tests, migrations, deployment files, and documentation inspected on 2026-09-29. Statuses assess implemented behavior—not roadmap intent. `COMPLETE` means complete for the documented demonstrator scope, not certification for safety-critical plant control.

## Executive summary

ProcessTwin already has a modular FastAPI/SQLAlchemy backend, organization membership authentication and RBAC, a SI-based CSTR model, synthetic data simulation, baseline sensor ingestion/quality logic, residual ML, bounded advisory optimization, dashboard, and Docker Compose deployment. Its pre-change CSV endpoint only accepted existing `sensor_id,timestamp,value,unit` readings; no historical dataset lifecycle existed. New work adds tenant-scoped CSV/optional Parquet dataset versions, configurable tag maps and SI units, preserved raw observations, quality reporting, and exploration endpoints. It remains a human-in-the-loop engineering platform: connectors do not control equipment and historical import is not a production historian integration.

## Readiness matrix

| Area | Status | Code evidence and assessment |
| --- | --- | --- |
| Architecture | PARTIAL | `docs/architecture/system.md`, `apps/api/processtwin_api/main.py`, `packages/*`, and `apps/*` separate API, physics, ML, optimization, simulator, and connectors; API lifespan still calls `Base.metadata.create_all`, so deployed migration discipline is not enforced by the service. |
| Database | PARTIAL | SQLAlchemy models in `apps/api/processtwin_api/models.py`, Alembic `migrations/versions/0001_initial_schema.py`, SQLite and PostgreSQL/Timescale Compose config exist. New dataset entities use tenant IDs, but DB-level row security and production migration/backup rehearsal are absent. |
| Authentication | PARTIAL | JWT login/refresh and bcrypt verification in `apps/api/processtwin_api/auth.py`; secret defaults are intentionally development-only and there is no OIDC integration or token revocation. |
| RBAC | PARTIAL | `RoleName`, `tenant_context`, `require_roles` and engineer/admin dependencies enforce route-level roles. Coverage is not a full route-by-route authorization matrix. |
| Tenant isolation | PARTIAL | Organization membership gates requests; queries for plants, sensors, datasets, and observations filter organization IDs. No database RLS; future queries must preserve this application boundary. |
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
| Dashboard | PARTIAL | `apps/web/components/historical-data-explorer.tsx` provides hierarchy selection, upload/mapping, SI summaries, sampled trends, gaps and quality display; calibration/evaluation/lifecycle/drift approval screens have not been added. |
| MQTT | PARTIAL | `connectors/mqtt/adapter.py` validates topic and JSON reading shape; broker lifecycle, delivery guarantees and operational hardening are absent. |
| OPC-UA | PARTIAL | `connectors/opcua/adapter.py` is explicitly read-only/disabled without configuration; no validated production server integration. |
| Observability | PARTIAL | API exposes health and Prometheus request/latency/ingestion metrics in `apps/api/processtwin_api/main.py`; distributed tracing, readiness checks and operational alert policies are not established. |
| Security | PARTIAL | `apps/api/processtwin_api/auth.py`, `audit.py`, API tenant queries and upload limits provide a baseline; production secrets, rate limiting, external identity, key rotation and security assurance remain deployment work. |
| Testing | PARTIAL | Tests now cover synthetic CSTR calibration recovery/bounds, irregular timestamp handling, evaluation residual metrics, chronological ML comparator scores, drift signals, validation gates and immutable production lifecycle in addition to data-import regression. No test is industrial evidence or a safety certification. |
| Deployment | PARTIAL | `Dockerfile.api`, app Dockerfiles, `docker-compose.yml`, `docker-compose.prod.yml`, Makefile, and `.github/workflows/ci.yml` provide a container demo and CI. Compose includes development credentials/defaults; production deployment, recovery and operations require hardening. |
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
- **TEST STATUS:** `58 passed, 1 skipped`; lint and migration pass; strict mypy retains 40 baseline errors. The skipped optional Parquet test and synthetic-only calibration fixtures do not establish industrial validation.

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
7. Resolve existing strict mypy errors and web tooling/runtime availability; expand authorization and migration upgrade tests.

## Validation record

- Python `pytest`: `58 passed, 1 skipped` (50.57s in post-commit verification). The optional Parquet adapter test skipped because local PyArrow could not import against the terminal NumPy build. Two existing deprecation warnings (Starlette/httpx and NumPy/joblib) remain.
- Ruff (`ruff check apps packages connectors tests`): passed.
- Alembic: `upgrade head` and `current` succeeded against fresh SQLite; current revision is `0003_calibration_registry`.
- Strict mypy (`mypy apps packages connectors`): 40 errors in 8 files remain; the phase-one baseline was also 40. The remaining diagnostics are in existing SciPy/sklearn/joblib/passlib stubs and pre-existing worker/API typing; new calibration/registry modules introduce no reported errors after resolving the NumPy boundary diagnostic.
- Frontend checks were not rerun: terminal Node/npm/pnpm are unavailable; no new frontend changes were made in this phase.
