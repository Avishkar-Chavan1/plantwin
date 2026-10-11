"use client";

import { FormEvent, useEffect, useState } from "react";
import { messageOf, readErrorMessage } from "./api-client";
import { Badge, HealthBadge, formatAge, formatNumber, formatTimestamp } from "./status";
import { useApiResource } from "./use-api-resource";
import { useWorkspaceData } from "./workspace-data";

type Sensor = { id: string; tag: string; name: string; unit: string; measurement_type: string };
type DataSource = {
  id: string; plant_id: string; name: string; source_type: string; endpoint: string; read_only: boolean; status: string;
  last_success_at: string | null; last_message_at: string | null; message_rate_per_minute: number; latency_ms: number | null;
  error_count: number; freshness_s: number | null; last_error: string | null; mappings: number; recent_quality: Record<string, number>;
};
type Mapping = { source_key: string; sensor_id: string; canonical_name: string; source_unit: string };

function QualitySummary({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
  if (!total) return <span className="muted">No readings in 15 min</span>;
  const good = counts.GOOD ?? 0;
  return <span>{formatNumber((good / total) * 100, 0)}% good<small>{Object.entries(counts).map(([name, value]) => `${name} ${value}`).join(" · ")}</small></span>;
}

export function LiveDataSources() {
  const { plants, apiFetch } = useWorkspaceData();
  const sources = useApiResource<{ items: DataSource[] }>("/api/v1/data-sources", { pollMs: 15_000 });
  const sensors = useApiResource<{ items: Sensor[] }>("/api/v1/sensors");
  const [plantId, setPlantId] = useState("");
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { setPlantId((current) => current || plants[0]?.id || ""); }, [plants]);

  function toggleMapping(sensor: Sensor, checked: boolean) {
    setMappings((previous) => checked
      ? [...previous.filter((item) => item.sensor_id !== sensor.id), { source_key: "", sensor_id: sensor.id, canonical_name: `reactor.${sensor.measurement_type}`, source_unit: sensor.unit }]
      : previous.filter((item) => item.sensor_id !== sensor.id));
  }
  function patchMapping(sensorId: string, patch: Partial<Mapping>) {
    setMappings((previous) => previous.map((item) => (item.sensor_id === sensorId ? { ...item, ...patch } : item)));
  }

  async function createSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setError("");
    setMessage("");
    if (!plantId) { setError("Select a plant before configuring a source."); return; }
    if (!mappings.length || mappings.some((mapping) => !mapping.source_key.trim())) { setError("Map at least one sensor and enter an explicit topic or OPC-UA node ID for each."); return; }
    setBusy(true);
    try {
      const response = await apiFetch("/api/v1/data-sources", {
        method: "POST",
        json: { plant_id: plantId, name: form.get("name"), source_type: form.get("source_type"), endpoint: form.get("endpoint"), stale_after_s: Number(form.get("stale_after_s")), mappings },
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "The data source could not be saved."));
      setMessage("Read-only source saved. It stays DISCONNECTED until the separately operated gateway reports a successful read.");
      formElement.reset();
      setMappings([]);
      await sources.reload();
    } catch (caught) {
      setError(messageOf(caught, "The data source could not be saved."));
    } finally {
      setBusy(false);
    }
  }

  if (sources.loading) return <div className="console-loading" role="status">Loading data sources…</div>;
  const items = sources.data?.items ?? [];
  return (
    <div className="data-sources-view">
      <p className="notice"><strong>Read-only acquisition.</strong> Sources subscribe to explicit tags and never write, set or command anything on the plant. Credentials and certificates are provisioned on the gateway through its environment or secret store, never in this UI or in endpoint URLs.</p>
      {message && <p className="message" role="status">{message}</p>}
      {(error || sources.error) && <div className="error-state" role="alert"><strong>Data sources.</strong> {error || sources.error}<button className="secondary" onClick={() => void sources.reload()}>Retry</button></div>}
      <section className="panel">
        <div className="panel-title">
          <div><p className="eyebrow">CONNECTOR HEALTH</p><h2>Configured sources</h2></div>
          <button type="button" className="secondary" onClick={() => void sources.reload()} disabled={sources.fetching}>{sources.fetching ? "Refreshing…" : "Refresh"}</button>
        </div>
        {items.length === 0 ? (
          <div className="empty-state"><h2>No live connector configured</h2><p>No telemetry is being acquired from a plant. Anything shown on the dashboard is simulation data or an imported historical file and is labelled as such.</p></div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source</th><th>Status</th><th>Last successful read</th><th>Last telemetry</th><th>Tags</th><th>Data quality (15 min)</th><th>Rate · latency</th><th>Errors</th></tr></thead>
              <tbody>
                {items.map((source) => (
                  <tr key={source.id}>
                    <td><strong>{source.name}</strong><small>{source.endpoint}</small><Badge tone="idle">{source.source_type} · {source.read_only ? "READ-ONLY" : "WRITE ENABLED"}</Badge></td>
                    <td><HealthBadge value={source.status} /></td>
                    <td>{source.last_success_at ? <>{formatAge(source.freshness_s)}<small>{formatTimestamp(source.last_success_at)}</small></> : <span className="muted">No successful read</span>}</td>
                    <td>{formatTimestamp(source.last_message_at)}</td>
                    <td className="num">{source.mappings}</td>
                    <td><QualitySummary counts={source.recent_quality ?? {}} /></td>
                    <td>{formatNumber(source.message_rate_per_minute, 1)}/min<small>{source.latency_ms !== null ? `${formatNumber(source.latency_ms, 0)} ms` : "— ms"}</small></td>
                    <td>{source.error_count}{source.last_error ? <small className="error-text">{source.last_error}</small> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <form className="panel import-form" onSubmit={createSource}>
        <p className="eyebrow">CONFIGURATION ONLY · NO AUTO-CONNECT</p>
        <h2>Add a read-only source</h2>
        <label>Plant<select value={plantId} onChange={(event) => { setPlantId(event.target.value); setMappings([]); }} required><option value="" disabled>Select a plant</option>{plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}</select></label>
        <label>Source name<input name="name" required maxLength={200} /></label>
        <label>Protocol<select name="source_type" defaultValue="MQTT"><option value="MQTT">MQTT subscriber</option><option value="OPCUA">OPC-UA read-only client</option></select></label>
        <label>Endpoint<input name="endpoint" required placeholder="mqtts://broker.example:8883  or  opc.tcp://server:4840" /></label>
        <label>Stale after (seconds)<input name="stale_after_s" type="number" min={1} max={86400} defaultValue={30} /></label>
        <p className="muted">Select existing sensors and enter the explicit topic or OPC-UA node ID that feeds each. Saving creates configuration only; it does not connect or publish.</p>
        <div className="mapping-list">
          {(sensors.data?.items ?? []).map((sensor) => {
            const existing = mappings.find((mapping) => mapping.sensor_id === sensor.id);
            return (
              <div className="mapping-row" key={sensor.id}>
                <label><input type="checkbox" checked={Boolean(existing)} onChange={(event) => toggleMapping(sensor, event.target.checked)} /> {sensor.tag} · {sensor.name} ({sensor.unit})</label>
                {existing && (
                  <>
                    <label>Topic / node ID<input value={existing.source_key} onChange={(event) => patchMapping(sensor.id, { source_key: event.target.value })} required /></label>
                    <label>Canonical name<input value={existing.canonical_name} onChange={(event) => patchMapping(sensor.id, { canonical_name: event.target.value })} required /></label>
                    <label>Source unit<input value={existing.source_unit} onChange={(event) => patchMapping(sensor.id, { source_unit: event.target.value })} required /></label>
                  </>
                )}
              </div>
            );
          })}
          {sensors.data && sensors.data.items.length === 0 && <p className="muted">No sensors exist yet. Create sensors for the equipment before mapping a source.</p>}
        </div>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save read-only source"}</button>
      </form>
    </div>
  );
}
