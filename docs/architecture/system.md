# ProcessTwin system architecture

```text
Sensor / CSV / MQTT / OPC UA simulator
              │ validated ingestion
              ▼
       tenant-scoped API ───► sensor readings / quality events
              │                         │
              ├────► CSTR physics ◄─────┤
              │          │               │
              │          ▼               ▼
              │       twin state     time-aware ML residual model
              │          │               │
              └──── simulation / constrained optimization ──► advisory recommendation
                                                         │
                                              audit log, metrics, dashboard/SSE
```

The API is the policy enforcement point: authentication establishes a user, membership establishes an organization and role, and every repository operation takes that organization ID from the authenticated context. Client request bodies cannot select another tenant.

## Delivery choices

The default developer database is SQLite for a one-command non-container demonstration. Compose deploys PostgreSQL with TimescaleDB support; the initial migration has plain PostgreSQL-compatible schema and documents the readings hypertable hook. Redis/MLflow/MinIO are service boundaries, not hidden requirements for physics or API tests.

Optimizers call the same physics simulation used by the twin. The ML component learns a residual over physics predictions, uses ordered train/validation/test partitions, persists feature schema alongside model metadata, and does not replace physics in an unqualified way.

No path reaches plant controls. Connector packages are constrained to data acquisition interfaces. `CONTROL_FEATURE_ENABLED` is deliberately absent and no command API exists.

