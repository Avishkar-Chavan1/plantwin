"use client";

import { FormEvent, useEffect, useState } from "react";

type Plant = { id: string; name: string };
type Sensor = { id: string; tag: string; name: string; unit: string; measurement_type: string; equipment_id?: string };
type DataSource = {
  id: string;
  plant_id: string;
  name: string;
  source_type: "MQTT" | "OPCUA";
  endpoint: string;
  read_only: boolean;
  status: "CONNECTED" | "DISCONNECTED" | "STALE" | "ERROR";
  last_success_at: string | null;
  message_rate_per_minute: number;
  latency_ms: number | null;
  error_count: number;
  freshness_s: number | null;
  last_error: string | null;
};
type Mapping = { enabled: boolean; source_key: string; sensor_id: string; canonical_name: string; source_unit: string };

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const nav = ["dashboard", "plants", "equipment", "sensors", "digital-twins", "simulations", "optimization", "recommendations", "alerts", "models", "data", "data-sources", "settings", "audit-log"];

export function LiveDataSources({ token, organization }: { token: string; organization: string }) {
  const headers = { Authorization: `Bearer ${token}`, "X-Organization-ID": organization };
  const [plants, setPlants] = useState<Plant[]>([]);
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [plantId, setPlantId] = useState("");
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [message, setMessage] = useState("No external connector is configured until an engineer creates a read-only source.");
  const [busy, setBusy] = useState(false);

  async function reload() {
    const [plantsResponse, sensorsResponse, sourcesResponse] = await Promise.all([
      fetch(`${apiUrl}/api/v1/plants`, { headers }),
      fetch(`${apiUrl}/api/v1/sensors`, { headers }),
      fetch(`${apiUrl}/api/v1/data-sources`, { headers }),
    ]);
    if (plantsResponse.ok) {
      const items: Plant[] = (await plantsResponse.json()).items;
      setPlants(items);
      if (!plantId && items.length) setPlantId(items[0].id);
    }
    if (sensorsResponse.ok) setSensors((await sensorsResponse.json()).items);
    if (sourcesResponse.ok) setSources((await sourcesResponse.json()).items);
  }

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => void reload(), 10_000);
    return () => window.clearInterval(timer);
  }, [token, organization]);

  function updateMapping(index: number, change: Partial<Mapping>) {
    setMappings((previous) => previous.map((mapping, itemIndex) => itemIndex === index ? { ...mapping, ...change } : mapping));
  }

  async function createSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selected = mappings.filter((mapping) => mapping.enabled && mapping.source_key && mapping.sensor_id);
    if (!selected.length) { setMessage("Select and map at least one sensor before configuring a source."); return; }
    setBusy(true);
    const response = await fetch(`${apiUrl}/api/v1/data-sources`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        plant_id: plantId,
        name: form.get("name"),
        source_type: form.get("source_type"),
        endpoint: form.get("endpoint"),
        stale_after_s: Number(form.get("stale_after_s")),
        mappings: selected.map(({ source_key, sensor_id, canonical_name, source_unit }) => ({ source_key, sensor_id, canonical_name, source_unit })),
      }),
    });
    const body = await response.json();
    setBusy(false);
    if (!response.ok) { setMessage(body.error?.message ?? "Data-source configuration failed."); return; }
    setMessage("Source configuration saved read-only. Start the separate ProcessTwin gateway to attempt connection; no LIVE status is assumed.");
    event.currentTarget.reset();
    setMappings([]);
    await reload();
  }

  return <main className="shell">
    <aside><div className="logo"><span>◈</span> ProcessTwin</div><p className="tenant">READ-ONLY CONNECTORS</p><nav>{nav.map((entry) => <a className={entry === "data-sources" ? "active" : ""} href={`/${entry}`} key={entry}>{entry.replaceAll("-", " ")}</a>)}</nav><div className="operator"><span className="dot" /> READ ONLY<br /><small>No publish or write API</small></div></aside>
    <section className="workspace"><header><div><p className="eyebrow">MQTT / OPC-UA · INGESTION GATEWAY</p><h1>Live data sources</h1></div><span className="pill">EXTERNAL CONNECTION NOT ASSUMED</span></header>
      <p className="notice">ProcessTwin exposes read-only subscriptions and reads. The UI has no PLC/DCS write or publish controls. Credentials are not accepted in endpoint URLs; configure transport credentials outside the web form.</p>
      <p className="message">{message}</p>
      <section className="panel"><div className="panel-title"><div><p className="eyebrow">ACTUAL CONNECTOR HEALTH</p><h2>Configured sources</h2></div><button type="button" className="secondary" onClick={() => void reload()}>Refresh</button></div>
        {sources.length ? <div style={{ overflowX: "auto" }}><table style={{ width: "100%", textAlign: "left" }}><thead><tr>{["Source", "Mode", "Status", "Last success", "Rate/min", "Latency ms", "Freshness s", "Errors", "Last error"].map((text) => <th key={text} style={{ padding: 9 }}>{text}</th>)}</tr></thead><tbody>{sources.map((source) => <tr key={source.id}><td>{source.name}<small style={{ display: "block" }}>{source.endpoint}</small></td><td>{source.source_type} · READ ONLY</td><td><span className={`pill ${source.status === "CONNECTED" ? "good" : ""}`}>{source.status}</span></td><td>{source.last_success_at ? new Date(source.last_success_at).toLocaleString() : "No successful read"}</td><td>{source.message_rate_per_minute.toFixed(2)}</td><td>{source.latency_ms?.toFixed(1) ?? "—"}</td><td>{source.freshness_s?.toFixed(1) ?? "—"}</td><td>{source.error_count}</td><td>{source.last_error ?? "—"}</td></tr>)}</tbody></table></div> : <p>No configured external sources. Demo readings are separately labelled SIMULATION MODE.</p>}
      </section>
      <form className="panel" onSubmit={createSource} style={{ display: "grid", gap: 14, marginTop: 18 }}><p className="eyebrow">CONFIGURATION ONLY · NO AUTO-CONNECT ON SAVE</p><h2>Add an external read-only source</h2>
        <label>Plant<select value={plantId} onChange={(event) => { setPlantId(event.target.value); setMappings([]); }} required><option value="" disabled>Select a tenant plant</option>{plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}</select></label>
        <label>Source name<input name="name" required maxLength={200} placeholder="Unit 1 OPC-UA read-only" /></label>
        <label>Protocol<select name="source_type" defaultValue="MQTT"><option value="MQTT">MQTT subscriber</option><option value="OPCUA">OPC-UA read-only client</option></select></label>
        <label>Endpoint<input name="endpoint" required placeholder="mqtts://broker.example:8883 or opc.tcp://server:4840" /></label>
        <label>Stale after (seconds)<input name="stale_after_s" type="number" min={1} max={86400} defaultValue={30} /></label>
        <p>Map explicit MQTT topics or OPC-UA node IDs to existing tenant sensors and canonical process variables.</p>
        {sensors.map((sensor) => {
          const existing = mappings.find((mapping) => mapping.sensor_id === sensor.id);
          const index = existing ? mappings.indexOf(existing) : -1;
          return <div key={sensor.id} className="controls" style={{ alignItems: "end" }}><label><input type="checkbox" checked={Boolean(existing?.enabled)} onChange={(event) => {
            if (event.target.checked && index < 0) setMappings((previous) => [...previous, { enabled: true, source_key: "", sensor_id: sensor.id, canonical_name: `reactor.${sensor.measurement_type}`, source_unit: sensor.unit }]);
            else if (!event.target.checked && index >= 0) setMappings((previous) => previous.filter((item) => item.sensor_id !== sensor.id));
          }} /> {sensor.tag} · {sensor.name} ({sensor.unit})</label>{existing && <><label>Topic / node ID<input value={existing.source_key} onChange={(event) => updateMapping(index, { source_key: event.target.value })} required /></label><label>Canonical name<input value={existing.canonical_name} onChange={(event) => updateMapping(index, { canonical_name: event.target.value })} required /></label><label>Source unit<input value={existing.source_unit} onChange={(event) => updateMapping(index, { source_unit: event.target.value })} required /></label></>}</div>;
        })}
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save read-only source"}</button>
      </form>
    </section>
  </main>;
}
