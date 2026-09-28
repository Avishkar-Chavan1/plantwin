# Physics calibration, model evaluation, and monitoring

This workflow uses **user-imported historical observations**. It does not mark the included synthetic simulator data as industrial evidence, and does not assert model validity merely because calibration or prediction executes successfully.

## CSTR audit and SI convention

`packages/physics/cstr.py` remains the first-order parallel reaction, perfectly mixed, constant-volume CSTR. State/feed temperature are K, pressure is Pa, volumetric flow is m³/s, concentrations are mol/m³, rate constants are s⁻¹, enthalpies J/mol, heat capacity J/(kg·K), and heat transfer W. The balances retain the equations in [the CSTR engineering note](engineering/cstr.md):

- $dC_A/dt=(F/V)(C_{A,in}-C_A)-r_1-r_2$; $r_1=k_1C_A$, $r_2=k_2C_A$.
- $k_i=k_{0,i}\exp(-E_{a,i}/RT)$.
- $\rho C_pV\,dT/dt=\rho C_pF(T_{in}-T)+(-\Delta H_1r_1-\Delta H_2r_2)V+UA(T_{cool}-T)$.

Feed sensible heat, both reaction heats, and the signed coolant/heating term are already part of the model. Pressure is stored/passed in SI but the incompressible liquid reference kinetics do not use it; pressure-dependent kinetics are not invented. Calibration can use uneven timestamp intervals: ODE samples align to actual times.

## Parameter-set versions and calibration

`GET /api/v1/physics/parameter-catalog` lists each SI parameter's current reference value, unit, min/max, initial value, description and source. These are model defaults, not plant-calibrated facts. `POST /api/v1/physics/parameter-sets` requires a complete validated definition for every catalog parameter and creates a new immutable tenant-scoped named version; it never overwrites a named set.

The import mappings required by the CSTR calibration dataset are:

| Canonical name | Normalized SI unit | Role |
| --- | --- | --- |
| `reactor.feed_flow` | m³/s or kg/s | Feed flow; kg/h source tags normalize to kg/s and convert to the model's required m³/s using the selected parameter-set density |
| `reactor.feed_temperature` | K | Feed temperature |
| `reactor.feed_concentration` | mol/m³ | Feed A concentration |
| `reactor.cooling_temperature` | K | Utility inlet temperature; higher values represent heating |
| `reactor.temperature` | K | Measured reactor temperature |
| `reactor.pressure` | Pa | Optional recorded context; not consumed by the current incompressible CSTR law |
| `reactor.concentration_a` | mol/m³ | Optional measured state target |
| `reactor.product_b`, `reactor.product_c` | mol/m³ | Optional measured product-state targets; `product_b` is required for the current hybrid yield workflow |

Every calibration sample must have the five required mapped values and `GOOD` quality. At least 10 strictly increasing, unique aligned timestamps are required. `POST /api/v1/calibrations` names the historical dataset version and initial parameter set, lists selected bounded parameters and optimizer, and optionally defines an operating envelope. `least_squares` uses bounded robust residual fitting; `differential_evolution` is available for multimodal objectives. The objective integrates the existing CSTR balances against the first chronological 60% only. The remaining 20% validation and 20% test windows are reported separately. Fitting both $U$ and $A$ simultaneously is rejected because this model observes their product $UA$, not the independent factors.

The import unit layer preserves source values and converts kg/h to kg/s. Since the CSTR material balance requires volumetric flow, the calibration boundary divides mass flow by the selected, versioned fluid density. When feed flow was mapped as mass flow, fitting density together is rejected because those coupled inputs are not independently observable in the current series representation.

A successful fit creates a new `CALIBRATED` parameter-set version and a `CALIBRATED` physics-only model version. Prior parameter sets remain intact. `GET /api/v1/calibrations` returns audit/provenance, bounds, initial/final values and computed train/validation/test scores.

## Evaluation and human validation

`POST /api/v1/models/{model_id}/evaluate` requires an independent imported dataset version (not the model's training version or a copy with the same file checksum) and a configured operating envelope. It returns measured values, physics predictions, residuals, MAE, RMSE, R² where defined, MAPE where meaningful, signed bias and residual percentiles. Evaluations are stored and available at `GET /api/v1/models/{model_id}/evaluations`.

Envelope keys explicitly encode units: `temperature_k`, `temperature_c`, `pressure_pa`, `pressure_bar`, `feed_flow_m3_s`, `feed_flow_kg_s`, or `feed_flow_kg_h`. Mass-flow checks use the density in the selected versioned parameter set to convert volumetric flow. Missing envelope, unavailable sensor data, or an input outside any configured bound prevents predictions; out-of-range observations are recorded with `OUTSIDE_VALIDATED_MODEL_RANGE` and no extrapolated results.

States are intentionally distinct: `CALIBRATED` records fitting only; `EVALUATED` records comparison to independent measurements; `VALIDATED` requires an authorized human reviewer, at least 10 independent observations, a configured envelope, numeric acceptance limits, and a written review note. A failed criterion cannot be promoted. Admins explicitly move `VALIDATED` → `STAGING` → `PRODUCTION`; prior production versions become `RETIRED`. Production and retired versions are immutable through evaluation/monitoring APIs.

## Physics + ML residual workflow

`POST /api/v1/models/hybrid/train` requires measured `reactor.product_b`. It computes dynamic CSTR product yield, trains a residual learner on actual-minus-physics yield using only the chronological 60% training partition, and fits a separately trained direct ML target model on the same training rows. It returns actual computed `MAE`, `RMSE`, `R²`, `MAPE` and bias for `physics_only`, `ml_only`, and `physics_plus_ml_residual` on train, validation and held-out test. No time-series row shuffle is used. Feature/target schema, exact time windows, data/parameter IDs, hyperparameters, bounds, envelope, creator/time and git SHA are recorded. A model remains `VALIDATION`; training does not validate or promote it.

The built-in SQL model registry is authoritative. Optional MLflow mirroring is enabled with `MLFLOW_TRACKING_URI` and the `mlflow` extra; an MLflow run ID/sync state is saved, while SQL retains tenant ownership and full lifecycle data. Offline or unavailable MLflow does not discard the SQL model version. This integration logs metadata/metrics, not a claim of a fully managed artifact registry.

## Drift monitoring

`POST /api/v1/models/{model_id}/drift` compares an imported current window to the training reference saved on the version. It reports feature, target, physics residual, prediction-error and (for hybrid artifacts with the measured target) hybrid residual PSI, two-sample KS, and Jensen–Shannon divergence metrics. Defaults are PSI ≥ 0.2, KS p-value < 0.01 together with JS divergence ≥ 0.1; thresholds are request-configurable. Each result is persisted and available at `GET /api/v1/models/{model_id}/drift-events`. A flag is `MODEL_DRIFT_DETECTED`; monitoring does not retrain, change the version state, or modify production artifacts. Drift thresholds are screening controls, not a site-approved alarm policy.

## API summary

- `GET /api/v1/physics/parameter-catalog`
- `GET/POST /api/v1/physics/parameter-sets`
- `POST /api/v1/calibrations`; `GET /api/v1/calibrations`
- `POST /api/v1/models/hybrid/train`
- `POST /api/v1/models/{id}/evaluate`; `GET /api/v1/models/{id}/evaluations`
- `POST /api/v1/models/{id}/validate`
- `POST /api/v1/models/{id}/stage`; existing admin promotion is `POST /api/v1/models/{id}/promote`
- `POST /api/v1/models/{id}/drift`; `GET /api/v1/models/{id}/drift-events`

All routes are authenticated and tenant-scoped; mutations require engineering role, except validation/staging/promotion, which require OWNER/ADMIN. Imports should follow [the historical data import guide](data-import.md).

## Validation boundary

Automated test trajectories are explicitly synthetic, deterministic fixtures for equations, bounds, chronological splitting, evaluation accounting, and lifecycle rules. No industrial dataset was supplied or evaluated in this task. The repository's model therefore remains **not independently plant validated** until authorized real observations, instrumentation assumptions, site envelope limits, and acceptance review are provided.
