# ProcessTwin

ProcessTwin is a human-in-the-loop industrial process digital twin for monitoring, simulation, anomaly review and advisory operating optimization. The included reference plant is a continuous stirred-tank reactor (CSTR) with parallel `A → B` (desired) and `A → C` (undesired) reactions.

> **Safety boundary:** ProcessTwin produces predictions, simulations and advisory recommendations. It does not communicate a control command to PLCs, DCSs, SCADA systems, pumps, valves, or other plant equipment. The control adapter feature flag is disabled by design.

## What is included

- A SI-unit CSTR dynamic/steady-state model validated against the analytical first-order CSTR result.
- A deterministic synthetic plant simulator with disturbances, sensor noise, drift, gaps and spikes.
- FastAPI REST API with JWT authentication, role-based authorization, tenant-scoped database queries, audit trail and Prometheus metrics.
- Sensor validation/data-quality classification, tenant-isolated CSV ingestion and streaming-ready connector interfaces.
- Time-aware ML training with residual (physics + ML) prediction, uncertainty estimates, model versions and feature importance.
- Constrained yield/energy optimization and explicitly advisory recommendations.
- Docker Compose services for PostgreSQL/TimescaleDB, Redis, MinIO, MLflow, API, simulator, Prometheus, Grafana and the Next.js dashboard.

## Quick start

For an entirely local developer run with SQLite:

```powershell
Copy-Item .env.example .env
python -m pip install -e ".[dev]"
make seed
make api
```

Open `http://localhost:8000/docs`. The demo API user is `engineer@processtwin.demo` with password `ChangeMeDemoOnly!`; change both values before any non-demo use.

For the container stack:

```powershell
Copy-Item .env.example .env
make demo
```

`make demo` is self-provisioning: it starts the infrastructure, applies the Alembic revision, creates the tenant/user/R-101/sensors, generates seven days of **simulated** history, registers a validation model, and starts the API, simulator, and dashboard. The dashboard is served on `http://localhost:3000`.

## Architecture

The Python packages are intentionally separate from delivery applications:

- `packages/physics` contains deterministic engineering models.
- `packages/units` owns conversion to/from SI units.
- `packages/twin` derives state, source metadata and health from measurements and physics.
- `packages/ml` handles time-series-safe residual learning.
- `packages/optimization` is a constrained, bounded optimizer over the validated operating envelope.
- `apps/api` owns HTTP, authentication, RBAC, tenant boundary, persistence and observability.
- `apps/simulator` generates explicitly simulated—not real—plant readings.
- `connectors` contain interfaces and validated adapters; they never imply live connectivity.

See [the system architecture](docs/architecture/system.md), [CSTR equations](docs/engineering/cstr.md), [API guide](docs/api/api.md), [security guide](docs/security/security.md) and [production deployment notes](docs/deployment/production.md).

Historical CSV and optional Parquet imports, configurable tag mapping, SI normalization, data-quality reporting and exploration are documented in [the historical data import guide](docs/data-import.md). Imported datasets are user-supplied and are distinct from the simulator's synthetic demo data.

Bounded CSTR parameter fitting, independent model evaluation, lifecycle gates, hybrid physics/ML comparisons and drift monitoring are documented in [the calibration and monitoring guide](docs/model-calibration.md). The framework has not yet been calibrated or validated against an industrial dataset.

## Development commands

| Command | Purpose |
| --- | --- |
| `make test` | Run unit, physics, API, ML and optimization tests. |
| `make lint` / `make typecheck` | Run Ruff / strict mypy. |
| `make simulate` | Generate a short simulated run. |
| `make train` | Train a versioned residual ML model using GOOD readings. |
| `make evaluate` | Print held-out time-based model metrics. |
| `make migrate` | Apply Alembic schema revisions. |

## Limitations and operating boundary

The reference model is a demonstrator calibrated to synthetic data, not a validated plant model. Model output outside its configured operating envelope is labelled as outside validated range. Any human implementation of a recommendation must be independently reviewed against operating procedures, hazard analysis and applicable regulatory requirements.

## License

MIT. See [LICENSE](LICENSE).
