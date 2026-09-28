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
| Physics | PARTIAL | `packages/physics/cstr.py` provides SI-input dynamic/steady-state balances, with analytical tests in `tests/physics/test_cstr.py`. The reference model is not validated/calibrated against plant data. |
| Simulator | COMPLETE (synthetic demo only) | `apps/simulator/processtwin_simulator/plant.py`, `run.py`, and `seed_demo.py` produce expressly simulated data and alerts. Not a real plant connector. |
| Data quality | PARTIAL | `apps/api/processtwin_api/quality.py` handles limits, finite values, order, duplicates and basic stuck/spike cases. New `packages/data_ingestion/pipeline.py` preserves row/value and applies missing, duplicate, order, limits, stuck, spike, rate, gap, timestamp, unit and drift rules to imported history. Heuristics/limits need site engineering review; baseline rejected REST bad measurements are still recorded as quality events rather than retained as readings. |
| ML | PARTIAL | `packages/ml/pipeline.py` and `apps/worker/processtwin_worker/train.py` provide time-aware residual fitting and model metadata; training currently expects seeded simulator sensor tags and synthetic yield labels. |
| Hybrid model | PARTIAL | Worker computes a physics yield baseline plus an ML residual; validation is synthetic and not an independently qualified process model. |
| Optimization | PARTIAL | `packages/optimization/service.py` bounds an advisory search against model constraints and API persistence; plant envelopes and human approval workflow require real site governance. |
| Dashboard | PARTIAL | `apps/web/components/process-twin-console.tsx` has login, simulated dashboard, scenario simulation, and advisory optimization; it does not implement full historical hierarchy selection, upload/mapping UI, or dataset exploration screens. |
| MQTT | PARTIAL | `connectors/mqtt/adapter.py` validates topic and JSON reading shape; broker lifecycle, delivery guarantees and operational hardening are absent. |
| OPC-UA | PARTIAL | `connectors/opcua/adapter.py` is explicitly read-only/disabled without configuration; no validated production server integration. |
| Observability | PARTIAL | API exposes health and Prometheus request/latency/ingestion metrics in `apps/api/processtwin_api/main.py`; distributed tracing, readiness checks and operational alert policies are not established. |
| Security | PARTIAL | `apps/api/processtwin_api/auth.py`, `audit.py`, API tenant queries and upload limits provide a baseline; production secrets, rate limiting, external identity, key rotation and security assurance remain deployment work. |
| Testing | PARTIAL | Existing physics, unit, ML, optimization, connector, migration, realtime, and API tests are supplemented with import, mapping, SI preservation, quality, API validation, exploration and tenant-isolation tests. Test coverage is not a certification suite. |
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

## What was already present

- Initial organization/user/membership schema, plants, process units, equipment, sensors, readings, quality events, audit history, and tenant-scoped API access.
- JWT authentication and five roles; CSTR SI physics, synthetic CSTR generator, REST reading and row-oriented CSV ingestion, bounded optimization, residual ML, MQTT/OPC-UA adapters, Next.js demo dashboard, Prometheus endpoint, Docker Compose, Alembic baseline, and CI workflow.
- Existing unit conversion already covered core temperature, pressure, flow, concentration, power and heat-capacity units; this work exposes canonical SI units and uses them in stored history rather than replacing that utility.

## Not represented as real industrial data

The included reference histories, default dashboard state, seeded labels and example sensor values are synthetic. No industrial process dataset is included. A user-imported dataset is treated as user-supplied historical data, but the application cannot verify its source, calibration, completeness, operating context, or regulatory chain of custody. Neither the synthetic demo nor an import constitutes process-model validation.

## Remaining work

1. Add a reviewed incremental Alembic migration for deployments already at revision `0001_initial_schema`; current initial migration is metadata-driven and does not upgrade an existing database with the newly added tables/columns. For clean demo databases, metadata creation sees the updated models.
2. Configure/install the optional `parquet` extra and run Parquet integration tests in CI; test for a missing extra returns a clear supported-format dependency error.
3. Add version mapping edits as immutable versioned records, checksummed object storage, chunked/background imports, idempotent retries and database-side tenant constraints.
4. Upgrade the frontend's sampled textual trend list to chart-based interaction and add per-tag sensor hierarchy assignment where a dataset contains multiple equipment items.
5. Add historian/REST/database read-only connectors, sampling-aware tests and production connector observability.
6. Independently review quality thresholds/drift algorithms with process engineers; establish audited calibration, model validation, alarms, response procedures, backup/recovery, threat review and deployment operations.
7. Resolve existing strict mypy errors and web tooling/runtime availability; expand authorization and migration upgrade tests.

## Validation record

- Python `pytest -q`: `35 passed, 1 skipped`. The optional Parquet adapter test skipped locally because the available PyArrow binary could not import against the terminal's NumPy (`numpy.core.multiarray failed to import`); CI now installs the `parquet` extra and will exercise it with a compatible clean environment. Existing Starlette/httpx and NumPy/joblib deprecation warnings remain.
- Ruff (`ruff check apps packages connectors tests`): passes after the final simulator import cleanup.
- Alembic: `upgrade head` succeeded against a fresh SQLite file, including revision `0002_historical_datasets`.
- Strict mypy (`mypy apps packages connectors`): still reports 40 errors in 8 existing source files. The baseline run before implementation reported the same 40 errors; no new errors originate in the added ingestion modules.
- Frontend: VS Code diagnostics report no errors in the changed TypeScript components. Terminal `npm`/`node`/`pnpm` are unavailable, so the web `lint`, `test`, and `build` scripts could not be executed here.
