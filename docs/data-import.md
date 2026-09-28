# Historical plant data import and exploration

Historical imports create immutable dataset versions in the selected tenant and plant. Supported files are UTF-8 CSV and Parquet (`.parquet` / `.pq`). CSV is available in the base install. Parquet requires `pip install -e ".[parquet]"` (installs Apache Arrow/PyArrow). Maximum upload size is controlled by `MAX_UPLOAD_BYTES` (default 5 MB), and a version is limited to 100,000 tabular rows. Larger imports need a future chunked/object-storage workflow.

If a file includes an optional per-tag unit column named `<source_tag>_unit` (for example `TI_101_unit`), it is validated dimensionally against the mapping and used for conversion; incompatible units are retained as `BAD` observations with `UNIT_MISMATCH`.

## Input and tag mapping

The file must have a timestamp column; each configured source-tag column is mapped to a canonical variable, engineering unit, and optionally a plant tag, process unit, equipment, engineering limits, expected sample interval, maximum rate, and drift threshold. No sensor names are hard-coded. All timestamps must be ISO-8601 and include a timezone; they are stored in UTC. Empty values remain observations marked `MISSING`.

Example CSV:

```csv
timestamp,TI_101,PI_101,FI_101,AI_101
2026-01-01T00:00:00Z,180.0,10.1,72.0,2.5
2026-01-01T00:01:00Z,180.2,10.0,71.8,2.5
```

Mapping JSON submitted as the `mappings` multipart form field:

```json
{
  "mappings": [
    {"source_tag":"TI_101","canonical_name":"reactor.temperature","unit":"degC","plant_tag":"TI_101","equipment_id":"<equipment UUID>","minimum_si":273.15,"maximum_si":573.15,"expected_sampling_interval_s":60},
    {"source_tag":"PI_101","canonical_name":"reactor.pressure","unit":"bar","plant_tag":"PI_101","equipment_id":"<equipment UUID>"},
    {"source_tag":"FI_101","canonical_name":"reactor.feed_flow","unit":"kg/h","plant_tag":"FI_101"},
    {"source_tag":"AI_101","canonical_name":"reactor.feed_concentration","unit":"mol/L","plant_tag":"AI_101"}
  ]
}
```

The unit map describes the source engineering unit. The normalization layer converts compatible units to SI (°C→K, bar/kPa→Pa, kg/h→kg/s, L/min→m³/s, kW→W, kJ/kg-K→J/kg-K, mol/L→mol/m³). Raw value and unit are retained; normalized values are separate. Dimensionally unsupported or unknown units reject the import mapping.

## Data-quality rules

Each source-tag/time row is retained with a status and one or more explicit reasons:

- `GOOD`: all applicable configured checks passed.
- `SUSPECT`: likely stuck signal, sudden spike, communication gap, unrealistic rate, or configured drift.
- `BAD`: missing/invalid/timezone-naive timestamp, duplicate/out-of-order timestamp, nonnumeric/nonfinite value, unit mismatch, or configured SI engineering limit violation.
- `MISSING`: blank/absent sensor value.

Configured bounds, sampling interval, maximum absolute rate-of-change, and maximum drift are optional mapping fields. Spike/stuck rules are conservative heuristics; review them against instrument resolution and process dynamics before operational use. Rows are never silently dropped. The report includes counts by status and reason. Import observations do not overwrite live `SensorReading` rows.

## API and engineer workflow

All endpoints require a bearer token, an authorized `X-Organization-ID`, and `OWNER`, `ADMIN`, or `ENGINEER` role.

1. Select a tenant plant (existing `GET /api/v1/plants`), then process unit/equipment identifiers from tenant records.
2. Upload a multipart file to `POST /api/v1/datasets/import` with `plant_id`, `dataset_name`, `mappings`, and `file`. Optional fields: `timestamp_column` (defaults to `timestamp`), `description`, and `dataset_id` to create a new version of an existing dataset.
3. List datasets at `GET /api/v1/datasets`.
4. List mapped variables at `GET /api/v1/datasets/{version_id}/variables`.
5. Explore summaries at `GET /api/v1/datasets/{version_id}/exploration` with optional `plant_id`, `process_unit_id`, `equipment_id`, `canonical_name`, timezone-aware `start`/`end`, and `limit` (1–5000).

Exploration returns engineering and normalized units, sampling rate, missingness, min/max/mean/population standard deviation, p05/p25/p50/p75/p95, quality counts, outliers, sampled trends, pairwise timestamp-aligned correlations, communication gaps, and the import quality summary. Trends and gaps are capped for response size. This API currently explores imported version observations; a full clickable dashboard workflow is not yet implemented.

REST and database historical source contracts are defined for future read-only adapters but are not connected to transport, credentials or query execution. MQTT/OPC-UA connectors remain separate acquisition adapters.

## Synthetic versus real industrial data

The demo seed and simulator create **SYNTHETIC DATA** only. Importing a file stores **USER-SUPPLIED HISTORICAL DATA** without independently verifying that it came from a plant, historian, calibrated instruments, or an authorized source. Do not present synthetic data as plant measurements, and do not treat imported values or derived model results as validated operating guidance.
