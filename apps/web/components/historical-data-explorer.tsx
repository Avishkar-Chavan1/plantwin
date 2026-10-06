"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Plant = { id: string; name: string };
type Hierarchy = { process_units: Array<{ id: string; name: string; unit_type: string }>; equipment: Array<{ id: string; name: string; tag: string; process_unit_id: string | null }> };
type Variable = { canonical_name: string; source_tags: string[]; engineering_unit: string; normalized_unit: string; sample_count: number; missing_count: number; unavailable_count: number; missingness_pct: number; sampling_rate_hz: number | null; min: number | null; max: number | null; mean: number | null; standard_deviation: number | null; percentiles: Record<string, number | null>; quality_counts: Record<string, number>; outliers: Array<{ timestamp: string; value: number; reasons: string[] }> };
type Exploration = { dataset_version: { id: string; version: number; row_count?: number; measurement_count?: number; source_filename?: string }; variables: Variable[]; correlation: Record<string, Record<string, number | null>>; trends: Record<string, Array<{ timestamp: string; value: number }>>; data_gaps: Array<{ variable: string; start: string; end: string; duration_s: number }> };
type Dataset = { id: string; name: string; plant_id: string; latest_version: { id: string; version: number; row_count: number; measurement_count: number; source_filename: string; created_at: string } | null };

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const MAX_UPLOAD_BYTES = 5_000_000;
const defaultMappings = JSON.stringify({ mappings: [
  { source_tag: "TI_101", canonical_name: "reactor.temperature", unit: "degC", plant_tag: "TI_101" },
  { source_tag: "PI_101", canonical_name: "reactor.pressure", unit: "bar", plant_tag: "PI_101" },
  { source_tag: "FI_101", canonical_name: "reactor.feed_flow", unit: "kg/h", plant_tag: "FI_101" },
] }, null, 2);

function display(value: number | null | undefined, digits = 3) { return value === null || value === undefined ? "—" : value.toFixed(digits); }

export function HistoricalDataExplorer({ token, organization }: { token: string; organization: string }) {
  const headers = useMemo(() => ({ Authorization: `Bearer ${token}`, "X-Organization-ID": organization }), [organization, token]);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [plantId, setPlantId] = useState("");
  const [hierarchy, setHierarchy] = useState<Hierarchy>({ process_units: [], equipment: [] });
  const [unitId, setUnitId] = useState("");
  const [equipmentId, setEquipmentId] = useState("");
  const [mappings, setMappings] = useState(defaultMappings);
  const [exploration, setExploration] = useState<Exploration | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [message, setMessage] = useState("Map historian tags to canonical variables, then import a CSV for server-side validation and exploration.");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [plantsResponse, datasetsResponse] = await Promise.all([
        fetch(`${apiUrl}/api/v1/plants?limit=100`, { headers }),
        fetch(`${apiUrl}/api/v1/datasets`, { headers }),
      ]);
      if (!plantsResponse.ok || !datasetsResponse.ok) throw new Error("Unable to load historical data metadata.");
      const plantItems = (await plantsResponse.json() as { items?: Plant[] }).items ?? [];
      setPlants(plantItems);
      setDatasets((await datasetsResponse.json() as { items?: Dataset[] }).items ?? []);
      setPlantId((current) => current || plantItems[0]?.id || "");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to load historical data metadata.");
    } finally { setLoading(false); }
  }, [headers]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    if (!plantId) { setHierarchy({ process_units: [], equipment: [] }); return; }
    const controller = new AbortController();
    void fetch(`${apiUrl}/api/v1/datasets/hierarchy?plant_id=${encodeURIComponent(plantId)}`, { headers, signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("Unable to load the plant hierarchy.");
      setHierarchy(await response.json() as Hierarchy);
      setUnitId(""); setEquipmentId("");
    }).catch((error) => { if (error.name !== "AbortError") setMessage(error.message); });
    return () => controller.abort();
  }, [headers, plantId]);

  const availableEquipment = hierarchy.equipment.filter((item) => !unitId || item.process_unit_id === unitId);
  const exploreVersion = useCallback(async (versionId: string) => {
    const query = new URLSearchParams();
    if (plantId) query.set("plant_id", plantId);
    if (unitId) query.set("process_unit_id", unitId);
    if (equipmentId) query.set("equipment_id", equipmentId);
    if (start) query.set("start", new Date(start).toISOString());
    if (end) query.set("end", new Date(end).toISOString());
    const response = await fetch(`${apiUrl}/api/v1/datasets/${versionId}/exploration?${query}`, { headers });
    const body = await response.json();
    if (!response.ok) { setMessage(body.detail?.message ?? body.error?.message ?? "Unable to explore this dataset version."); return; }
    setExploration(body as Exploration);
  }, [equipmentId, end, headers, plantId, start, unitId]);

  async function importDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const file = form.get("file");
    if (!(file instanceof File) || !file.size) { setMessage("Choose a non-empty CSV or Parquet file."); return; }
    if (file.size > MAX_UPLOAD_BYTES) { setMessage("This deployment accepts files up to 5 MB. Use the chunked import workflow for larger files."); return; }
    try {
      const parsed = JSON.parse(String(form.get("mappings"))) as { mappings?: Array<Record<string, unknown>> };
      if (!Array.isArray(parsed.mappings) || parsed.mappings.length === 0) throw new Error("Mapping JSON must contain a non-empty mappings array.");
      parsed.mappings = parsed.mappings.map((mapping) => ({ ...mapping, ...(unitId && !mapping.process_unit_id ? { process_unit_id: unitId } : {}), ...(equipmentId && !mapping.equipment_id ? { equipment_id: equipmentId } : {}) }));
      form.set("mappings", JSON.stringify(parsed));
    } catch (error) { setMessage(error instanceof Error ? error.message : "Invalid tag mapping JSON."); return; }
    if (!form.get("plant_id")) { setMessage("Select a plant before importing."); return; }
    setBusy(true); setMessage("Uploading and validating the dataset…");
    try {
      const response = await fetch(`${apiUrl}/api/v1/datasets/import`, { method: "POST", headers, body: form });
      const body = await response.json();
      if (!response.ok) { setMessage(body.detail?.message ?? body.error?.message ?? "Historical import failed."); return; }
      setMessage(`Imported ${body.version.row_count} rows and ${body.version.measurement_count} measurements. Dataset version ${body.version.version} is stored.`);
      await reload();
      await exploreVersion(body.version.id);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Historical import failed."); }
    finally { setBusy(false); }
  }

  if (loading && plants.length === 0) return <div className="console-loading" role="status">Loading data workspace…</div>;
  return <div className="data-view">
    <section className="panel"><div className="panel-title"><div><p className="eyebrow">HISTORICAL DATA</p><h2>Import and explore</h2></div><button type="button" className="secondary" onClick={() => void reload()}>Refresh</button></div>
      <p className="notice">Uploaded values are retained with their original engineering units and quality reasons. Imports are read-only historical records; they never become plant control commands.</p>
      <p className="message" role="status">{message}</p>
      <form onSubmit={importDataset} className="import-form">
        <label>Plant<select name="plant_id" value={plantId} onChange={(event) => setPlantId(event.target.value)} required><option value="" disabled>Select a plant</option>{plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}</select></label>
        <label>Process unit<select value={unitId} onChange={(event) => { setUnitId(event.target.value); setEquipmentId(""); }}><option value="">All process units</option>{hierarchy.process_units.map((unit) => <option key={unit.id} value={unit.id}>{unit.name} · {unit.unit_type}</option>)}</select></label>
        <label>Equipment<select value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)}><option value="">All equipment</option>{availableEquipment.map((item) => <option key={item.id} value={item.id}>{item.tag} · {item.name}</option>)}</select></label>
        <label>Dataset name<input name="dataset_name" required maxLength={200} placeholder="Reactor trial · 2026-10-06" /></label>
        <label>Timestamp column<input name="timestamp_column" defaultValue="timestamp" required /></label>
        <label>Tag mappings (JSON)<textarea name="mappings" required rows={8} value={mappings} onChange={(event) => setMappings(event.target.value)} /></label>
        <label>CSV or Parquet file<input name="file" type="file" accept=".csv,.parquet,.pq" required /></label>
        <button type="submit" disabled={busy}>{busy ? "Importing…" : "Import and inspect"}</button><small>CSV/Parquet up to 5 MB in this form. Timestamps must include a timezone.</small>
      </form>
    </section>
    <section className="panel"><div className="panel-title"><div><p className="eyebrow">STORED DATASETS</p><h2>Dataset versions</h2></div></div>{datasets.length === 0 ? <div className="empty-state">No historical datasets have been imported for this organization.</div> : <div className="dataset-list">{datasets.map((dataset) => <button className="dataset-row" key={dataset.id} onClick={() => dataset.latest_version && void exploreVersion(dataset.latest_version.id)} disabled={!dataset.latest_version}><span><strong>{dataset.name}</strong><small>{dataset.latest_version?.source_filename ?? "No version"}</small></span><span>{dataset.latest_version ? `v${dataset.latest_version.version} · ${dataset.latest_version.row_count} rows` : "Empty"}</span></button>)}</div>}</section>
    {exploration && <ExplorationView exploration={exploration} start={start} end={end} setStart={setStart} setEnd={setEnd} onApply={() => void exploreVersion(exploration.dataset_version.id)} />}
  </div>;
}

function ExplorationView({ exploration, start, end, setStart, setEnd, onApply }: { exploration: Exploration; start: string; end: string; setStart: (value: string) => void; setEnd: (value: string) => void; onApply: () => void }) {
  const chartVariables = Object.entries(exploration.trends).slice(0, 3);
  return <section className="panel exploration"><div className="panel-title"><div><p className="eyebrow">DATASET VERSION {exploration.dataset_version.version}</p><h2>Quality and trends</h2></div><span className="pill">{exploration.variables.length} variables</span></div>
    <div className="controls"><label>Start<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label><label>End<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} /></label><button type="button" onClick={onApply}>Apply time range</button></div>
    <div className="trend-grid">{chartVariables.map(([name, points]) => <article className="trend-card" key={name}><h3>{name}</h3><ResponsiveContainer width="100%" height={180}><LineChart data={points}><XAxis dataKey="timestamp" hide /><YAxis width={48} tick={{ fill: "#86a0ae", fontSize: 10 }} domain={["auto", "auto"]} /><Tooltip labelFormatter={(label) => new Date(String(label)).toLocaleString()} /><Line dataKey="value" stroke="#60d5c6" dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer></article>)}</div>
    <div className="table-wrap"><table><thead><tr>{["Variable", "Samples / missing", "Min / max", "Mean ± σ", "p05 / p50 / p95", "Rate", "Quality"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>{exploration.variables.map((variable) => <tr key={variable.canonical_name}><td><strong>{variable.canonical_name}</strong><small>{variable.source_tags.join(", ")}</small></td><td>{variable.sample_count} / {variable.missing_count}<small>{variable.unavailable_count} unusable</small></td><td>{display(variable.min)} / {display(variable.max)} {variable.normalized_unit}</td><td>{display(variable.mean)} ± {display(variable.standard_deviation)}</td><td>{display(variable.percentiles.p05)} / {display(variable.percentiles.p50)} / {display(variable.percentiles.p95)}</td><td>{display(variable.sampling_rate_hz, 4)} Hz</td><td>{Object.entries(variable.quality_counts).map(([quality, count]) => `${quality}: ${count}`).join(" · ")}</td></tr>)}</tbody></table></div>
    <div className="grid bottom"><article><h3>Data gaps</h3>{exploration.data_gaps.length ? exploration.data_gaps.slice(0, 100).map((gap, index) => <p key={`${gap.variable}-${index}`} className="muted">{gap.variable}: {gap.start} → {gap.end} ({Math.round(gap.duration_s)} s)</p>) : <p className="muted">No communication gaps detected in the selected range.</p>}</article><article><h3>Correlation</h3><pre className="data-json">{JSON.stringify(exploration.correlation, null, 2)}</pre></article></div>
  </section>;
}
