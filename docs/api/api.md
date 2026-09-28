# API guide

Interactive OpenAPI documentation is available at `/docs`.

Authentication uses bearer tokens issued by `POST /api/v1/auth/login`. The access token carries a user ID only; organization and role are resolved from `X-Organization-ID` against database membership for each request. A supplied organization ID is therefore a selector within authorized memberships, never a trust boundary bypass.

Important endpoints:

- `GET /api/v1/plants` lists accessible plants.
- `POST /api/v1/ingestion/readings` records validated readings and data-quality events.
- `POST /api/v1/datasets/import` imports a tenant-scoped CSV or optional Parquet history using explicit tag mappings; `GET /api/v1/datasets/{version_id}/exploration` returns unit-aware statistics, trends, correlation, gaps and quality results.
- `GET /api/v1/datasets/hierarchy` returns the selected tenant plant's process units and equipment for import mapping.
- `GET /api/v1/physics/parameter-catalog` and `GET/POST /api/v1/physics/parameter-sets` define immutable CSTR parameter-set versions.
- `POST /api/v1/calibrations` fits bounded CSTR parameters to GOOD historical observations; `GET /api/v1/calibrations` lists auditable runs.
- `POST /api/v1/models/hybrid/train` compares physics-only, direct ML-only and residual-hybrid models on chronological windows.
- `POST /api/v1/models/{model_id}/evaluate` requires an independent dataset version and an operating envelope; evaluation history is at `/evaluations`.
- `POST /api/v1/models/{model_id}/validate`, `/stage`, and `/promote` implement explicit human/admin lifecycle gates.
- `POST /api/v1/models/{model_id}/drift` compares an imported window with model references; `/drift-events` returns persisted results. Drift never auto-retrains.
- `POST /api/v1/simulations` runs a physics what-if scenario; no physical control action occurs.
- `POST /api/v1/optimization/runs` computes a bounded advisory scenario.
- `GET /api/v1/dashboard/summary` returns current twin and alert information.

Errors use `{ "error": { "code", "message", "request_id" } }` with no stack trace.

See [historical data import and exploration](../data-import.md) for multipart fields, mapping JSON and quality semantics.
See [physics calibration, evaluation and monitoring](../model-calibration.md) for required canonical CSTR tags, SI parameter bounds, status gates and the no-industrial-validation caveat. Optional MLflow metadata logging uses the `mlflow` extra and `MLFLOW_TRACKING_URI`; tenant-scoped SQL records remain the authoritative registry.

