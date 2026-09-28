# API guide

Interactive OpenAPI documentation is available at `/docs`.

Authentication uses bearer tokens issued by `POST /api/v1/auth/login`. The access token carries a user ID only; organization and role are resolved from `X-Organization-ID` against database membership for each request. A supplied organization ID is therefore a selector within authorized memberships, never a trust boundary bypass.

Important endpoints:

- `GET /api/v1/plants` lists accessible plants.
- `POST /api/v1/ingestion/readings` records validated readings and data-quality events.
- `POST /api/v1/datasets/import` imports a tenant-scoped CSV or optional Parquet history using explicit tag mappings; `GET /api/v1/datasets/{version_id}/exploration` returns unit-aware statistics, trends, correlation, gaps and quality results.
- `GET /api/v1/datasets/hierarchy` returns the selected tenant plant's process units and equipment for import mapping.
- `POST /api/v1/simulations` runs a physics what-if scenario; no physical control action occurs.
- `POST /api/v1/optimization/runs` computes a bounded advisory scenario.
- `GET /api/v1/dashboard/summary` returns current twin and alert information.

Errors use `{ "error": { "code", "message", "request_id" } }` with no stack trace.

See [historical data import and exploration](../data-import.md) for multipart fields, mapping JSON and quality semantics.

