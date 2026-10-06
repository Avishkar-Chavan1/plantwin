"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Plant = { id: string; name: string };
type Sensor = { id: string; tag: string; name: string; unit: string; measurement_type: string };
type DataSource = { id: string; plant_id: string; name: string; source_type: "MQTT" | "OPCUA"; endpoint: string; read_only: boolean; status: string; last_success_at: string | null; message_rate_per_minute: number; latency_ms: number | null; error_count: number; freshness_s: number | null; last_error: string | null; mappings: number };
type Mapping = { source_key: string; sensor_id: string; canonical_name: string; source_unit: string };
const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function LiveDataSources({ token, organization }: { token: string; organization: string }) {
  const headers = useMemo(() => ({ Authorization: `Bearer ${token}`, "X-Organization-ID": organization }), [organization, token]);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [plantId, setPlantId] = useState("");
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [message, setMessage] = useState("External connectors are optional. Configure only explicit read-only subscriptions; no publish or control API is exposed here.");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [plantsResponse, sensorsResponse, sourcesResponse] = await Promise.all([
        fetch(`${apiUrl}/api/v1/plants?limit=100`, { headers }),
        fetch(`${apiUrl}/api/v1/sensors`, { headers }),
        fetch(`${apiUrl}/api/v1/data-sources`, { headers }),
      ]);
      if (!plantsResponse.ok || !sensorsResponse.ok || !sourcesResponse.ok) throw new Error("Unable to load connector metadata.");
      const plantItems = (await plantsResponse.json() as { items?: Plant[] }).items ?? [];
      setPlants(plantItems); setPlantId((current) => current || plantItems[0]?.id || "");
      setSensors((await sensorsResponse.json() as { items?: Sensor[] }).items ?? []);
      setSources((await sourcesResponse.json() as { items?: DataSource[] }).items ?? []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Unable to load connector metadata."); }
    finally { setLoading(false); }
  }, [headers]);
  useEffect(() => { void reload(); }, [reload]);

  function updateMapping(sensor: Sensor, checked: boolean) {
    setMappings((previous) => checked ? [...previous.filter((item) => item.sensor_id !== sensor.id), { source_key: "", sensor_id: sensor.id, canonical_name: `reactor.${sensor.measurement_type}`, source_unit: sensor.unit }] : previous.filter((item) => item.sensor_id !== sensor.id));
  }

  async function createSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (!plantId) { setMessage("Select a plant before configuring a source."); return; }
    if (!mappings.length || mappings.some((mapping) => !mapping.source_key.trim())) { setMessage("Map at least one sensor and enter an explicit topic or OPC-UA node ID."); return; }
    setBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/v1/data-sources`, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ plant_id: plantId, name: form.get("name"), source_type: form.get("source_type"), endpoint: form.get("endpoint"), stale_after_s: Number(form.get("stale_after_s")), mappings }) });
      const body = await response.json();
      if (!response.ok) { setMessage(body.detail?.message ?? body.error?.message ?? "Data-source configuration failed."); return; }
      setMessage("Read-only source configuration saved. Connection health will remain DISCONNECTED until the separately operated gateway reports a successful read.");
      event.currentTarget.reset(); setMappings([]); await reload();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Data-source configuration failed."); }
    finally { setBusy(false); }
  }

  if (loading && sources.length === 0) return <div className="console-loading" role="status">Loading data sources…</div>;
  return <div className="data-sources-view">
    <p className="notice">All configured sources are read-only. Credentials must be provisioned outside this UI; endpoint URLs cannot contain credentials, query secrets, or write commands.</p>
    <p className="message" role="status">{message}</p>
    <section className="panel"><div className="panel-title"><div><p className="eyebrow">CONNECTOR HEALTH</p><h2>Configured sources</h2></div><button type="button" className="secondary" onClick={() => void reload()}>Refresh</button></div>
      {sources.length ? <div className="table-wrap"><table><thead><tr>{["Source", "Protocol", "Status", "Last success", "Rate/min", "Latency", "Freshness", "Errors", "Last error"].map((text) => <th key={text}>{text}</th>)}</tr></thead><tbody>{sources.map((source) => <tr key={source.id}><td><strong>{source.name}</strong><small>{source.endpoint}</small></td><td>{source.source_type} · READ ONLY</td><td><span className={`pill ${source.status === "CONNECTED" ? "good" : ""}`}>{source.status}</span></td><td>{source.last_success_at ? new Date(source.last_success_at).toLocaleString() : "No successful read"}</td><td>{source.message_rate_per_minute.toFixed(2)}</td><td>{source.latency_ms?.toFixed(1) ?? "—"} ms</td><td>{source.freshness_s?.toFixed(1) ?? "—"} s</td><td>{source.error_count}</td><td>{source.last_error ?? "—"}</td></tr>)}</tbody></table></div> : <div className="empty-state">No configured external sources. Simulation and imported historical data are shown with explicit source labels.</div>}
    </section>
    <form className="panel import-form" onSubmit={createSource}><p className="eyebrow">CONFIGURATION ONLY · NO AUTO-CONNECT</p><h2>Add a read-only source</h2>
      <label>Plant<select value={plantId} onChange={(event) => { setPlantId(event.target.value); setMappings([]); }} required><option value="" disabled>Select a plant</option>{plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}</select></label>
      <label>Source name<input name="name" required maxLength={200} placeholder="Unit 1 historian" /></label><label>Protocol<select name="source_type" defaultValue="MQTT"><option value="MQTT">MQTT subscriber</option><option value="OPCUA">OPC-UA read-only client</option></select></label><label>Endpoint<input name="endpoint" required placeholder="mqtts://broker.example:8883 or opc.tcp://server:4840" /></label><label>Stale after (seconds)<input name="stale_after_s" type="number" min={1} max={86400} defaultValue={30} /></label>
      <p className="muted">Select existing tenant sensors and map explicit source keys. Saving creates configuration only; it does not connect or publish.</p>
      <div className="mapping-list">{sensors.map((sensor) => { const index = mappings.findIndex((mapping) => mapping.sensor_id === sensor.id); const existing = index >= 0 ? mappings[index] : undefined; return <div className="mapping-row" key={sensor.id}><label><input type="checkbox" checked={Boolean(existing)} onChange={(event) => updateMapping(sensor, event.target.checked)} /> {sensor.tag} · {sensor.name} ({sensor.unit})</label>{existing && <><label>Topic / node ID<input value={existing.source_key} onChange={(event) => setMappings((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, source_key: event.target.value } : item))} required /></label><label>Canonical name<input value={existing.canonical_name} onChange={(event) => setMappings((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, canonical_name: event.target.value } : item))} required /></label><label>Source unit<input value={existing.source_unit} onChange={(event) => setMappings((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, source_unit: event.target.value } : item))} required /></label></>}</div>; })}</div>
      <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save read-only source"}</button>
    </form>
  </div>;
}
