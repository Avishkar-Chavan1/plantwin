"use client";

import { FormEvent, useEffect, useState } from "react";

type Plant = { id: string; name: string };
type Hierarchy = {
  process_units: Array<{ id: string; name: string; unit_type: string }>;
  equipment: Array<{ id: string; name: string; tag: string; process_unit_id: string | null }>;
};
type Variable = {
  canonical_name: string;
  source_tags: string[];
  engineering_unit: string;
  normalized_unit: string;
  sample_count: number;
  missing_count: number;
  unavailable_count: number;
  missingness_pct: number;
  sampling_rate_hz: number | null;
  min: number | null;
  max: number | null;
  mean: number | null;
  standard_deviation: number | null;
  percentiles: Record<string, number | null>;
  quality_counts: Record<string, number>;
  outliers: Array<{ timestamp: string; value: number; reasons: string[] }>;
};
type Exploration = {
  dataset_version: { id: string; version: number };
  variables: Variable[];
  correlation: Record<string, Record<string, number | null>>;
  trends: Record<string, Array<{ timestamp: string; value: number }>>;
  data_gaps: Array<{ variable: string; start: string; end: string; duration_s: number }>;
};

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const defaultMappings = JSON.stringify({ mappings: [
  { source_tag: "TI_101", canonical_name: "reactor.temperature", unit: "degC", plant_tag: "TI_101" },
  { source_tag: "PI_101", canonical_name: "reactor.pressure", unit: "bar", plant_tag: "PI_101" },
  { source_tag: "FI_101", canonical_name: "reactor.feed_flow", unit: "kg/h", plant_tag: "FI_101" },
  { source_tag: "AI_101", canonical_name: "reactor.feed_concentration", unit: "mol/L", plant_tag: "AI_101" },
]}, null, 2);

function display(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

export function HistoricalDataExplorer({ token, organization }: { token: string; organization: string }) {
  const headers = { Authorization: `Bearer ${token}`, "X-Organization-ID": organization };
  const [plants, setPlants] = useState<Plant[]>([]);
  const [plantId, setPlantId] = useState("");
  const [hierarchy, setHierarchy] = useState<Hierarchy>({ process_units: [], equipment: [] });
  const [unitId, setUnitId] = useState("");
  const [equipmentId, setEquipmentId] = useState("");
  const [mappings, setMappings] = useState(defaultMappings);
  const [exploration, setExploration] = useState<Exploration | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [message, setMessage] = useState("Choose a plant, map its historian tags, then import a CSV or Parquet file.");

  useEffect(() => {
    void fetch(`${apiUrl}/api/v1/plants`, { headers }).then(async (response) => {
      if (!response.ok) { setMessage("Unable to load plants for the selected organization."); return; }
      const items: Plant[] = (await response.json()).items;
      setPlants(items);
      if (items.length) setPlantId(items[0].id);
    });
  }, [organization, token]);

  useEffect(() => {
    if (!plantId) { setHierarchy({ process_units: [], equipment: [] }); return; }
    void fetch(`${apiUrl}/api/v1/datasets/hierarchy?plant_id=${encodeURIComponent(plantId)}`, { headers })
      .then(async (response) => {
        if (!response.ok) { setMessage("Unable to load process-unit and equipment hierarchy."); return; }
        setHierarchy(await response.json());
        setUnitId("");
        setEquipmentId("");
      });
  }, [plantId, organization, token]);

  const availableEquipment = hierarchy.equipment.filter((item) => !unitId || item.process_unit_id === unitId);

  async function exploreVersion(versionId: string) {
    const query = new URLSearchParams();
    if (plantId) query.set("plant_id", plantId);
    if (unitId) query.set("process_unit_id", unitId);
    if (equipmentId) query.set("equipment_id", equipmentId);
    if (start) query.set("start", new Date(start).toISOString());
    if (end) query.set("end", new Date(end).toISOString());
    const response = await fetch(`${apiUrl}/api/v1/datasets/${versionId}/exploration?${query}`, { headers });
    const body = await response.json();
    if (!response.ok) { setMessage(body.error?.message ?? "Unable to explore this dataset version."); return; }
    setExploration(body);
  }

  async function importDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const parsed = JSON.parse(String(form.get("mappings")));
      if (!Array.isArray(parsed.mappings)) throw new Error("Mapping JSON must contain a mappings array.");
      parsed.mappings = parsed.mappings.map((mapping: Record<string, unknown>) => ({
        ...mapping,
        ...(unitId && !mapping.process_unit_id ? { process_unit_id: unitId } : {}),
        ...(equipmentId && !mapping.equipment_id ? { equipment_id: equipmentId } : {}),
      }));
      form.set("mappings", JSON.stringify(parsed));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Invalid tag mapping JSON.");
      return;
    }
    const response = await fetch(`${apiUrl}/api/v1/datasets/import`, { method: "POST", headers, body: form });
    const body = await response.json();
    if (!response.ok) { setMessage(body.error?.message ?? "Historical import failed."); return; }
    setMessage(`Imported ${body.version.row_count} rows and ${body.version.measurement_count} measurements as version ${body.version.version}.`);
    await exploreVersion(body.version.id);
  }

  return <main className="shell">
    <aside><div className="logo"><span>◈</span> ProcessTwin</div><p className="tenant">TENANT HISTORICAL DATA</p><nav>{["dashboard", "plants", "equipment", "sensors", "digital-twins", "simulations", "optimization", "recommendations", "alerts", "models", "data", "settings", "audit-log"].map((entry) => <a className={entry === "data" ? "active" : ""} href={`/${entry === "dashboard" ? "dashboard" : entry}`} key={entry}>{entry.replaceAll("-", " ")}</a>)}</nav><div className="operator"><span className="dot" /> Human-in-the-loop<br /><small>Read-only historical import</small></div></aside>
    <section className="workspace"><header><div><p className="eyebrow">PLANT → PROCESS UNIT → EQUIPMENT → SENSOR → TIME</p><h1>Historical data</h1></div><span className="pill good">SI NORMALIZED</span></header>
      <p className="notice">Original engineering values and quality reasons are retained. Uploaded data is user-supplied and is not independently verified as real plant data.</p><p className="message">{message}</p>
      <form className="panel" onSubmit={importDataset} style={{ display: "grid", gap: 14 }}><h2>Import a dataset version</h2>
        <label>Plant<select name="plant_id" value={plantId} onChange={(event) => setPlantId(event.target.value)} required><option value="" disabled>Select a tenant plant</option>{plants.map((plant) => <option value={plant.id} key={plant.id}>{plant.name}</option>)}</select></label>
        <label>Process unit<select value={unitId} onChange={(event) => { setUnitId(event.target.value); setEquipmentId(""); }}><option value="">All process units</option>{hierarchy.process_units.map((unit) => <option value={unit.id} key={unit.id}>{unit.name} ({unit.unit_type})</option>)}</select></label>
        <label>Equipment<select value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)}><option value="">All equipment</option>{availableEquipment.map((item) => <option value={item.id} key={item.id}>{item.tag} — {item.name}</option>)}</select></label>
        <label>Dataset name<input name="dataset_name" required maxLength={200} placeholder="Reactor trial, week 1" /></label>
        <label>Timestamp column<input name="timestamp_column" defaultValue="timestamp" /></label>
        <label>Tag mappings (JSON)<textarea name="mappings" required rows={10} value={mappings} onChange={(event) => setMappings(event.target.value)} /></label>
        <label>CSV or Parquet file<input name="file" type="file" accept=".csv,.parquet,.pq" required /></label>
        <button type="submit">Import and inspect</button><small>Parquet requires the server's optional pyarrow extra. Timestamps must include a timezone.</small>
      </form>
      {exploration && <section className="panel" style={{ marginTop: 18, overflowX: "auto" }}><div className="panel-title"><div><p className="eyebrow">DATASET VERSION {exploration.dataset_version.version}</p><h2>Variable summaries and trends</h2></div><span className="pill">{exploration.variables.length} variables</span></div>
        <div className="controls"><label>Start<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label><label>End<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} /></label><button type="button" onClick={() => void exploreVersion(exploration.dataset_version.id)}>Apply time range</button></div>
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}><thead><tr>{["Variable", "Source → SI", "Samples / missing", "Missing %", "Min / max", "Mean ± σ", "p05 / p50 / p95", "Rate Hz", "Quality"].map((label) => <th key={label} style={{ padding: 10, color: "var(--muted)" }}>{label}</th>)}</tr></thead><tbody>{exploration.variables.map((variable) => <tr key={variable.canonical_name}><td style={{ padding: 10 }}>{variable.canonical_name}<small style={{ display: "block" }}>{variable.source_tags.join(", ")}</small></td><td style={{ padding: 10 }}>{variable.engineering_unit} → {variable.normalized_unit}</td><td style={{ padding: 10 }}>{variable.sample_count} / {variable.missing_count}<small style={{ display: "block" }}>{variable.unavailable_count} unusable</small></td><td style={{ padding: 10 }}>{display(variable.missingness_pct, 1)}%</td><td style={{ padding: 10 }}>{display(variable.min)} / {display(variable.max)}</td><td style={{ padding: 10 }}>{display(variable.mean)} ± {display(variable.standard_deviation)}</td><td style={{ padding: 10 }}>{display(variable.percentiles.p05)} / {display(variable.percentiles.p50)} / {display(variable.percentiles.p95)}</td><td style={{ padding: 10 }}>{display(variable.sampling_rate_hz, 4)}</td><td style={{ padding: 10 }}>{Object.entries(variable.quality_counts).map(([quality, count]) => `${quality}: ${count}`).join(" · ")}</td></tr>)}</tbody></table>
        <div className="grid bottom"><article><h3>Trend samples</h3>{Object.entries(exploration.trends).map(([name, points]) => <details key={name}><summary>{name} ({points.length} points)</summary><div style={{ maxHeight: 220, overflow: "auto" }}>{points.slice(-100).map((point, index) => <p key={`${point.timestamp}-${index}`}>{point.timestamp} · {display(point.value)} {exploration.variables.find((item) => item.canonical_name === name)?.normalized_unit}</p>)}</div></details>)}</article><article><h3>Correlation</h3><pre style={{ overflow: "auto", maxHeight: 300 }}>{JSON.stringify(exploration.correlation, null, 2)}</pre></article></div>
        <h3>Outliers and data gaps</h3>{exploration.variables.flatMap((variable) => variable.outliers.map((item, index) => <p key={`${variable.canonical_name}-outlier-${index}`}>Outlier · {variable.canonical_name} · {item.timestamp} · {display(item.value)} {variable.normalized_unit} · {item.reasons.join(", ")}</p>))}{exploration.data_gaps.length ? exploration.data_gaps.slice(0, 100).map((gap, index) => <p key={`${gap.variable}-${index}`}>Gap · {gap.variable}: {gap.start} → {gap.end} ({Math.round(gap.duration_s)} s)</p>) : <p>No communication gaps detected in the selected range.</p>}
      </section>}
    </section>
  </main>;
}
