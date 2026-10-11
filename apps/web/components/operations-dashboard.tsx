"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, HealthBadge, ModeBadge, formatAge, formatNumber, formatTimestamp, modeInfo, normalizeMode } from "./status";
import { useApiResource } from "./use-api-resource";
import { useWorkspaceData } from "./workspace-data";

type Measurement = {
  name: string;
  measurement_type: string;
  value: number;
  unit: string;
  quality_status: string;
  source: string;
  source_mode: string;
  timestamp: string;
  age_s: number;
  stale: boolean | null;
};
type Estimate = { value: number; unit: string; source: string } | null;
type Summary = {
  plant?: { id: string; name: string } | null;
  equipment?: { id: string; tag: string; name: string } | null;
  source_mode?: string;
  plant_health?: string;
  data_state?: string;
  measurements?: Record<string, Measurement>;
  twin?: {
    timestamp: string;
    temperature: Estimate;
    conversion: Estimate;
    yield: Estimate;
    selectivity: Estimate;
    heat_removal: Estimate;
    prediction_status: string;
    withheld_reason?: string;
    model_version_id: string | null;
  } | null;
  data_quality?: { sensor_count: number; sensors_with_data: number; missing_tags: string[]; stale_tags: string[]; suspect_tags: string[] };
  connectors?: { count: number; items: Array<{ id: string; name: string; source_type: string; status: string; read_only: boolean; last_success_at: string | null; freshness_s: number | null; tags: number }> };
  alerts?: { open_count: number; items: Array<{ id: string; severity: string; reason: string; timestamp: string }> };
  models?: { count: number; active: { name: string; version: string; type: string; status: string; has_operating_envelope: boolean } | null; validation_note: string };
  recommendations?: { pending_review_count: number; items: Array<{ id: string; text: string; status: string; timestamp: string }> };
  safety_notice?: string;
  message?: string;
};
type SensorItem = { id: string; equipment_id: string; tag: string; name: string; unit: string };
type ReadingItem = { timestamp: string; value: number; quality_status: string };

const SOURCE_LABEL: Record<string, string> = { MQTT: "MQTT", OPCUA: "OPC-UA", SIMULATED: "Simulator", CSV: "CSV import", PARQUET: "Parquet import", REST: "REST import", DATABASE: "Database import" };

export function OperationsDashboard() {
  const { summary: raw, isLoading } = useWorkspaceData();
  const summary = raw as Summary | null;
  if (isLoading && !summary) return <div className="console-loading" role="status">Loading process data…</div>;
  if (!summary) return <section className="panel empty-state"><h2>Dashboard unavailable</h2><p>The process summary could not be loaded. See the message above, then retry.</p></section>;
  return <DashboardBody summary={summary} />;
}

function DashboardBody({ summary }: { summary: Summary }) {
  const mode = normalizeMode(summary.source_mode);
  const measurements = useMemo(() => Object.entries(summary.measurements ?? {}).sort(([, a], [, b]) => a.measurement_type.localeCompare(b.measurement_type) || a.name.localeCompare(b.name)), [summary.measurements]);
  const quality = summary.data_quality;
  const connectors = summary.connectors?.items ?? [];
  const connected = connectors.filter((item) => item.status === "CONNECTED").length;

  return (
    <div className="dashboard">
      <section className="kpi-row" aria-label="Key status">
        <article className="kpi">
          <p>Plant health</p>
          <div className="kpi-value"><HealthBadge value={summary.plant_health} /></div>
          <small>{summary.equipment ? `${summary.equipment.tag} · ${summary.equipment.name}` : "No equipment configured"}</small>
        </article>
        <article className="kpi">
          <p>Data source</p>
          <div className="kpi-value"><ModeBadge mode={mode} /></div>
          <small>{modeInfo(mode).description}</small>
        </article>
        <article className="kpi">
          <p>Active alarms</p>
          <div className="kpi-value kpi-number">{summary.alerts?.open_count ?? 0}</div>
          <small><Link href="/alerts">Open alerts</Link></small>
        </article>
        <article className="kpi">
          <p>Data quality</p>
          <div className="kpi-value kpi-number">{quality ? `${quality.sensors_with_data}/${quality.sensor_count}` : "—"}</div>
          <small>{quality ? `${quality.stale_tags.length} stale · ${quality.suspect_tags.length} suspect · ${quality.missing_tags.length} no data` : "No sensors"}</small>
        </article>
        <article className="kpi">
          <p>Connectors</p>
          <div className="kpi-value kpi-number">{connectors.length ? `${connected}/${connectors.length}` : "None"}</div>
          <small>{connectors.length ? "connected · read-only" : "No live connector configured"}</small>
        </article>
        <article className="kpi">
          <p>Recommendations</p>
          <div className="kpi-value kpi-number">{summary.recommendations?.pending_review_count ?? 0}</div>
          <small><Link href="/recommendations">Awaiting engineer review</Link></small>
        </article>
      </section>

      <section className="panel" aria-labelledby="conditions-title">
        <div className="panel-title">
          <div><p className="eyebrow">CURRENT OPERATING CONDITIONS</p><h2 id="conditions-title">Latest values</h2></div>
          <ModeBadge mode={mode} />
        </div>
        {measurements.length === 0 ? (
          <div className="empty-state">
            <h2>{summary.equipment ? "Waiting for telemetry" : "No data available"}</h2>
            <p>{summary.message ?? summary.safety_notice ?? "No measurements are stored for this equipment."}</p>
            <p><Link href="/data-sources">Configure a read-only connector</Link> or <Link href="/data">import historical data</Link>.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Variable</th><th>Value</th><th>Quality</th><th>Source</th><th>Updated</th><th>State</th></tr></thead>
              <tbody>
                {measurements.map(([tag, item]) => (
                  <tr key={tag}>
                    <td><strong>{item.name}</strong><small>{tag} · {item.measurement_type}</small></td>
                    <td className="num">{formatNumber(item.value, 2)} <span className="muted">{item.unit}</span></td>
                    <td><Badge tone={item.quality_status === "GOOD" ? "live" : "warn"}>{item.quality_status}</Badge></td>
                    <td>{SOURCE_LABEL[item.source] ?? item.source}<small>{item.source_mode.replaceAll("_", " ")}</small></td>
                    <td title={formatTimestamp(item.timestamp)}>{mode === "HISTORICAL" ? formatTimestamp(item.timestamp) : formatAge(item.age_s)}</td>
                    <td>{item.stale ? <Badge tone="warn">STALE DATA</Badge> : item.stale === false ? <Badge tone="live">CURRENT</Badge> : <Badge tone="hist">ARCHIVAL</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {quality && quality.missing_tags.length > 0 && measurements.length > 0 && <p className="notice compact">No data yet for: {quality.missing_tags.join(", ")}.</p>}
      </section>

      <div className="dashboard-grid">
        <TrendPanel summary={summary} mode={mode} />
        <section className="panel" aria-labelledby="twin-title">
          <p className="eyebrow">MODEL ESTIMATES · NOT MEASURED</p>
          <h2 id="twin-title">Twin state</h2>
          <TwinEstimates twin={summary.twin} mode={mode} />
        </section>
      </div>

      <div className="dashboard-grid three">
        <section className="panel">
          <p className="eyebrow">ACTIVE ALARMS</p>
          <h2>Alarms</h2>
          {summary.alerts && summary.alerts.items.length > 0 ? (
            <ul className="plain-list">
              {summary.alerts.items.map((alert) => (
                <li key={alert.id}><Badge tone={alert.severity === "CRITICAL" ? "bad" : alert.severity === "WARNING" ? "warn" : "idle"}>{alert.severity}</Badge> {alert.reason}<small>{formatTimestamp(alert.timestamp)}</small></li>
              ))}
            </ul>
          ) : <p className="muted">No open alarms.</p>}
        </section>
        <section className="panel">
          <p className="eyebrow">CONNECTOR STATUS</p>
          <h2>Data sources</h2>
          {connectors.length === 0 ? (
            <p className="muted">No live connector is configured. {mode === "SIMULATION" ? "Values shown come from the simulator." : "Telemetry is not being acquired from a plant."}</p>
          ) : (
            <ul className="plain-list">
              {connectors.map((item) => (
                <li key={item.id}><strong>{item.name}</strong> <Badge tone="idle">{item.source_type} · READ-ONLY</Badge> <HealthBadge value={item.status} />
                  <small>{item.tags} tags · last success {item.last_success_at ? formatTimestamp(item.last_success_at) : "never"}</small></li>
              ))}
            </ul>
          )}
        </section>
        <section className="panel">
          <p className="eyebrow">MODEL STATUS</p>
          <h2>Models</h2>
          {summary.models?.active ? (
            <p><strong>{summary.models.active.name}</strong> v{summary.models.active.version} <Badge tone={summary.models.active.status === "PRODUCTION" ? "live" : "hist"}>{summary.models.active.status}</Badge></p>
          ) : <p><Badge tone="warn">UNVALIDATED REFERENCE MODEL</Badge></p>}
          <p className="muted">{summary.models?.validation_note}</p>
          {summary.recommendations && summary.recommendations.items.length > 0 && (
            <>
              <h3>Pending recommendations</h3>
              <ul className="plain-list">
                {summary.recommendations.items.map((item) => <li key={item.id}>{item.text}<small>{item.status} · advisory, requires engineer review</small></li>)}
              </ul>
            </>
          )}
        </section>
      </div>
      <p className="notice compact">{summary.safety_notice}</p>
    </div>
  );
}

function TwinEstimates({ twin, mode }: { twin: Summary["twin"]; mode: ReturnType<typeof normalizeMode> }) {
  if (!twin) return <div className="empty-state"><h2>{mode === "NO_DATA" ? "No data available" : "Waiting for telemetry"}</h2><p>No synchronized twin state exists yet. Estimates appear once all required model inputs are mapped and reporting.</p></div>;
  const rows: Array<[string, Estimate]> = [["Conversion", twin.conversion], ["Yield", twin.yield], ["Selectivity", twin.selectivity], ["Heat removal", twin.heat_removal]];
  return (
    <>
      <dl className="state-list">
        {rows.map(([label, estimate]) => <div key={label}><dt>{label}</dt><dd>{estimate ? `${formatNumber(estimate.value, 1)} ${estimate.unit}` : "No data available"}</dd></div>)}
        <div><dt>Prediction status</dt><dd>{twin.prediction_status.replaceAll("_", " ")}</dd></div>
        <div><dt>Evaluated</dt><dd>{formatTimestamp(twin.timestamp)}</dd></div>
      </dl>
      {twin.withheld_reason && <p className="warning">{twin.withheld_reason}</p>}
      {twin.prediction_status === "UNVALIDATED_PHYSICS_REFERENCE" && <p className="notice compact">Estimates use an unvalidated reference model. They are not validated against a plant.</p>}
      {twin.prediction_status === "OUTSIDE_VALIDATED_MODEL_RANGE" && <Badge tone="warn">OUTSIDE VALIDATED RANGE</Badge>}
    </>
  );
}

function TrendPanel({ summary, mode }: { summary: Summary; mode: ReturnType<typeof normalizeMode> }) {
  const equipmentId = summary.equipment?.id;
  const sensors = useApiResource<{ items: SensorItem[] }>(equipmentId ? "/api/v1/sensors" : null);
  const equipmentSensors = useMemo(() => (sensors.data?.items ?? []).filter((sensor) => sensor.equipment_id === equipmentId), [sensors.data, equipmentId]);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    setSelected((current) => equipmentSensors.some((sensor) => sensor.id === current) ? current : equipmentSensors[0]?.id ?? "");
  }, [equipmentSensors]);
  const sensor = equipmentSensors.find((item) => item.id === selected);
  // Live trends refresh every 30 s while visible; archival and simulated history is fetched once.
  const readings = useApiResource<{ items: ReadingItem[] }>(selected ? `/api/v1/sensors/${selected}/readings?limit=120` : null, { pollMs: mode === "LIVE_READ_ONLY" ? 30_000 : undefined });
  const points = useMemo(() => [...(readings.data?.items ?? [])].reverse().map((reading) => ({ time: new Date(reading.timestamp).getTime(), value: reading.value })), [readings.data]);

  return (
    <section className="panel" aria-labelledby="trend-title">
      <div className="panel-title">
        <div><p className="eyebrow">RECENT TREND · {mode === "LIVE_READ_ONLY" ? "LIVE READ-ONLY" : mode === "SIMULATION" ? "SIMULATED" : mode === "HISTORICAL" ? "HISTORICAL" : "NO DATA"}</p><h2 id="trend-title">{sensor?.name ?? "Trend"}</h2></div>
        {equipmentSensors.length > 0 && (
          <label className="compact-control"><span className="sr-only">Trend variable</span>
            <select value={selected} onChange={(event) => setSelected(event.target.value)}>
              {equipmentSensors.map((item) => <option key={item.id} value={item.id}>{item.tag} · {item.name}</option>)}
            </select>
          </label>
        )}
      </div>
      {sensors.error ? <div className="error-state" role="alert">{sensors.error}</div>
        : sensors.loading || readings.loading ? <div className="empty-state">Loading measured history…</div>
        : readings.error ? <div className="error-state" role="alert">{readings.error}</div>
        : points.length === 0 ? <div className="empty-state"><h2>No data available</h2><p>No readings are stored for this variable.</p></div>
        : (
          <div className="plot" role="img" aria-label={`${sensor?.name ?? "Sensor"} trend, ${points.length} samples`}>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#17384e" strokeDasharray="3 3" />
                <XAxis dataKey="time" type="number" scale="time" domain={["dataMin", "dataMax"]} tick={{ fill: "#86a0ae", fontSize: 11 }} minTickGap={36} tickFormatter={(value) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} />
                <YAxis tick={{ fill: "#86a0ae", fontSize: 11 }} width={54} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ background: "#0b2131", border: "1px solid #31536a", color: "#e7f1f5" }} labelFormatter={(label) => new Date(Number(label)).toLocaleString()} formatter={(value) => [`${formatNumber(Number(value), 2)} ${sensor?.unit ?? ""}`, sensor?.name ?? "value"]} />
                <Line type="monotone" dataKey="value" stroke={mode === "SIMULATION" ? "#f2b36d" : "#60d5c6"} dot={false} strokeWidth={2} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
            <small className="chart-caption">{points.length} stored samples · {sensor?.unit ?? "engineering units"} · {mode === "SIMULATION" ? "SIMULATED values" : mode === "LIVE_READ_ONLY" ? "read-only acquisition" : "stored readings"}</small>
          </div>
        )}
    </section>
  );
}
