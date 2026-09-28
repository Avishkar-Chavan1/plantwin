# API guide

Interactive OpenAPI documentation is available at `/docs`.

Authentication uses bearer tokens issued by `POST /api/v1/auth/login`. The access token carries a user ID only; organization and role are resolved from `X-Organization-ID` against database membership for each request. A supplied organization ID is therefore a selector within authorized memberships, never a trust boundary bypass.

Important endpoints:

- `GET /api/v1/plants` lists accessible plants.
- `POST /api/v1/ingestion/readings` records validated readings and data-quality events.
- `POST /api/v1/simulations` runs a physics what-if scenario; no physical control action occurs.
- `POST /api/v1/optimization/runs` computes a bounded advisory scenario.
- `GET /api/v1/dashboard/summary` returns current twin and alert information.

Errors use `{ "error": { "code", "message", "request_id" } }` with no stack trace.

