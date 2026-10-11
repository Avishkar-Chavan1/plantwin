"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { messageOf, readErrorMessage } from "./api-client";
import { Badge, formatNumber, formatTimestamp } from "./status";
import { useApiResource } from "./use-api-resource";
import { useWorkspaceData } from "./workspace-data";

type Hierarchy = { process_units: Array<{ id: string; name: string; unit_type: string }>; equipment: Array<{ id: string; name: string; tag: string; process_unit_id: string | null }> };
type Variable = { canonical_name: string; source_tags: string[]; normalized_unit: string; sample_count: number; missing_count: number; unavailable_count: number; sampling_rate_hz: number | null; min: number | null; max: number | null; mean: number | null; standard_deviation: number | null; percentiles: Record<string, number | null>; quality_counts: Record<string, number> };
type Exploration = { dataset_version: { id: string; version: number }; variables: Variable[]; correlation: Record<string, Record<string, number | null>>; trends: Record<string, Array<{ timestamp: string; value: number }>>; data_gaps: Array<{ variable: string; start: string; end: string; duration_s: number }> };
type Dataset = { id: string; name: string; plant_id: string; latest_version: { id: string; version: number; row_count: number; measurement_count: number; source_filename: string; created_at: string; quality_summary?: { status_counts?: Record<string, number> } } | null };

type Column = { name: string; kind: "timestamp" | "numeric" | "text" | "empty"; non_empty_count: number; empty_count: number; sample: string[] };
type Suggestion = { source_tag: string; canonical_name: string | null; unit: string | null; plant_tag: string; suggested: boolean };
type Preview = {
  filename: string;
  row_count: number;
  columns: Column[];
  timestamp_column: string | null;
  timestamp: { parsed_count: number; no_timezone_count: number; invalid_count: number; duplicate_count: number; first: string | null; last: string | null; issues: string[] } | null;
  suggested_mappings: Suggestion[];
  sample_rows: Array<Record<string, string>>;
  supported_units: Record<string, string>;
  blocking_errors: string[];
  validation: { measurements: number; status_counts: Record<string, number>; reason_counts: Record<string, number>; usable_fraction: number; warning: string | null } | null;
};
type Row = { source_tag: string; include: boolean; canonical_name: string; unit: string; suggested: boolean };

const MAX_UPLOAD_BYTES = 5_000_000;
const UPLOAD_TIMEOUT_MS = 120_000;
const COMMON_ZONES = ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "America/Los_Angeles", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"];

function display(value: number | null | undefined, digits = 3) { return value === null || value === undefined ? "—" : value.toFixed(digits); }

export function HistoricalDataExplorer() {
  const { plants, apiFetch, setMessage } = useWorkspaceData();
  const datasets = useApiResource<{ items: Dataset[] }>("/api/v1/datasets");
  const [plantId, setPlantId] = useState("");
  useEffect(() => { setPlantId((current) => current || plants[0]?.id || ""); }, [plants]);
  const hierarchy = useApiResource<Hierarchy>(plantId ? `/api/v1/datasets/hierarchy?plant_id=${encodeURIComponent(plantId)}` : null);
  const [unitId, setUnitId] = useState("");
  const [equipmentId, setEquipmentId] = useState("");

  const [file, setFile] = useState<File | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [timestampColumn, setTimestampColumn] = useState("");
  const [timezone, setTimezone] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState<"" | "analyzing" | "importing">("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");

  const [exploration, setExploration] = useState<Exploration | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  const availableEquipment = (hierarchy.data?.equipment ?? []).filter((item) => !unitId || item.process_unit_id === unitId);
  const chosen = rows.filter((row) => row.include);
  const mappingPayload = useMemo(() => JSON.stringify({
    mappings: chosen.map((row) => ({
      source_tag: row.source_tag, canonical_name: row.canonical_name.trim(), unit: row.unit, plant_tag: row.source_tag,
      ...(unitId ? { process_unit_id: unitId } : {}), ...(equipmentId ? { equipment_id: equipmentId } : {}),
    })),
  }), [chosen, equipmentId, unitId]);
  const rowProblems = chosen.filter((row) => !row.canonical_name.trim() || !row.unit);

  const analyze = useCallback(async (selected: File, options: { tsColumn?: string; zone?: string; mappings?: string }) => {
    setBusy("analyzing");
    setError("");
    try {
      const form = new FormData();
      form.set("file", selected);
      if (options.tsColumn) form.set("timestamp_column", options.tsColumn);
      if (options.zone) form.set("assume_timezone", options.zone);
      if (options.mappings) form.set("mappings", options.mappings);
      const response = await apiFetch("/api/v1/datasets/preview", { method: "POST", body: form, timeoutMs: UPLOAD_TIMEOUT_MS });
      if (!response.ok) throw new Error(await readErrorMessage(response, "The file could not be analyzed."));
      const body = (await response.json()) as Preview;
      setPreview(body);
      setTimestampColumn(body.timestamp_column ?? "");
      setRows((previous) => previous.length ? previous : body.suggested_mappings.map((item) => ({ source_tag: item.source_tag, include: item.suggested, canonical_name: item.canonical_name ?? "", unit: item.unit ?? "", suggested: item.suggested })));
    } catch (caught) {
      setPreview(null);
      setError(messageOf(caught, "The file could not be analyzed."));
    } finally {
      setBusy("");
    }
  }, [apiFetch]);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setPreview(null); setRows([]); setResult(""); setError(""); setFile(null);
    if (!selected) return;
    if (selected.size === 0) { setError("The selected file is empty."); return; }
    if (selected.size > MAX_UPLOAD_BYTES) { setError(`This deployment accepts files up to ${MAX_UPLOAD_BYTES / 1_000_000} MB (this one is ${(selected.size / 1_000_000).toFixed(1)} MB). Split the file by time range.`); return; }
    setFile(selected);
    setDatasetName((current) => current || selected.name.replace(/\.[^.]+$/, ""));
    void analyze(selected, {});
  }

  // Re-run server-side validation whenever the mapping, timestamp column or zone changes.
  useEffect(() => {
    if (!file || !preview) return;
    const timer = window.setTimeout(() => { void analyze(file, { tsColumn: timestampColumn, zone: timezone, mappings: chosen.length ? mappingPayload : undefined }); }, 400);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mappingPayload, timestampColumn, timezone]);

  async function importDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !preview) return;
    setBusy("importing"); setError(""); setResult("");
    try {
      const form = new FormData();
      form.set("file", file);
      form.set("plant_id", plantId);
      form.set("dataset_name", datasetName.trim());
      form.set("timestamp_column", timestampColumn);
      form.set("mappings", mappingPayload);
      if (timezone) form.set("assume_timezone", timezone);
      const response = await apiFetch("/api/v1/datasets/import", { method: "POST", body: form, timeoutMs: UPLOAD_TIMEOUT_MS });
      if (!response.ok) throw new Error(await readErrorMessage(response, "The import failed."));
      const body = await response.json() as { version: { id: string; version: number; row_count: number; measurement_count: number }; quality_report: { status_counts: Record<string, number> }; unmapped_source_tags?: string[]; warnings?: string[] };
      const counts = body.quality_report.status_counts;
      const ignored = body.unmapped_source_tags ?? [];
      setResult(`Imported ${body.version.row_count} rows / ${body.version.measurement_count} measurements as dataset version ${body.version.version}. Quality: ${Object.entries(counts).filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n}`).join(", ")}.${ignored.length ? ` Not imported (unmapped columns): ${ignored.join(", ")}.` : ""}`);
      if (body.warnings?.length) setError(body.warnings.join(" "));
      setMessage("Historical import completed. Values are stored as historical data and never mixed with live readings.");
      setFile(null); setPreview(null); setRows([]);
      await datasets.reload();
      await explore(body.version.id);
    } catch (caught) {
      setError(messageOf(caught, "The import failed."));
    } finally {
      setBusy("");
    }
  }

  const explore = useCallback(async (versionId: string) => {
    const query = new URLSearchParams();
    if (plantId) query.set("plant_id", plantId);
    if (start) query.set("start", new Date(start).toISOString());
    if (end) query.set("end", new Date(end).toISOString());
    try {
      const response = await apiFetch(`/api/v1/datasets/${versionId}/exploration?${query}`);
      if (!response.ok) throw new Error(await readErrorMessage(response, "Unable to explore this dataset version."));
      setExploration((await response.json()) as Exploration);
    } catch (caught) {
      setError(messageOf(caught, "Unable to explore this dataset version."));
    }
  }, [apiFetch, end, plantId, start]);

  const unitOptions = Object.keys(preview?.supported_units ?? {});
  const canImport = Boolean(preview && file && chosen.length > 0 && rowProblems.length === 0 && datasetName.trim() && plantId && timestampColumn && preview.blocking_errors.length === 0 && busy === "");

  if (datasets.loading) return <div className="console-loading" role="status">Loading data workspace…</div>;
  return (
    <div className="data-view">
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">HISTORICAL DATA</p><h2>Upload a CSV or Parquet file</h2></div><Badge tone="hist">HISTORICAL · NEVER LIVE</Badge></div>
        <p className="notice">Imported values are archival records with their original units and per-value quality flags. They are kept separate from live and simulated data and cannot become plant commands.</p>
        {result && <p className="message" role="status">{result}</p>}
        {error && <div className="error-state" role="alert"><strong>Upload.</strong> {error}</div>}
        <form className="import-form" onSubmit={importDataset}>
          <fieldset className="step"><legend>1 · Choose a file</legend>
            <label>Plant<select value={plantId} onChange={(event) => { setPlantId(event.target.value); setUnitId(""); setEquipmentId(""); }} required><option value="" disabled>Select a plant</option>{plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}</select></label>
            <label>CSV or Parquet file (max {MAX_UPLOAD_BYTES / 1_000_000} MB)<input type="file" accept=".csv,.parquet,.pq" onChange={chooseFile} disabled={busy !== ""} /></label>
            {busy === "analyzing" && <p className="muted" role="status">Analyzing file…</p>}
          </fieldset>

          {preview && (
            <>
              <fieldset className="step"><legend>2 · Check what was detected</legend>
                <p><strong>{preview.filename}</strong> · {preview.row_count.toLocaleString()} rows · {preview.columns.length} columns</p>
                <div className="table-wrap"><table>
                  <thead><tr><th>Column</th><th>Detected type</th><th>Filled</th><th>Sample values</th></tr></thead>
                  <tbody>{preview.columns.map((column) => <tr key={column.name}><td><strong>{column.name}</strong></td><td><Badge tone={column.kind === "numeric" ? "live" : column.kind === "timestamp" ? "hist" : "idle"}>{column.kind.toUpperCase()}</Badge></td><td>{column.non_empty_count}/{preview.row_count}</td><td className="muted">{column.sample.join(" · ") || "—"}</td></tr>)}</tbody>
                </table></div>
                <div className="controls">
                  <label>Timestamp column<select value={timestampColumn} onChange={(event) => setTimestampColumn(event.target.value)} required><option value="" disabled>Select the timestamp column</option>{preview.columns.map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}</select></label>
                  <label>Time zone of timestamps without an offset<select value={timezone} onChange={(event) => setTimezone(event.target.value)}><option value="">Reject timestamps without an offset</option>{COMMON_ZONES.map((zone) => <option key={zone} value={zone}>{zone}</option>)}</select></label>
                </div>
                {preview.timestamp && (
                  <p className="muted">{preview.timestamp.parsed_count}/{preview.row_count} timestamps parsed{preview.timestamp.first ? ` · ${formatTimestamp(preview.timestamp.first)} → ${formatTimestamp(preview.timestamp.last)}` : ""}</p>
                )}
                {preview.timestamp?.issues.map((issue) => <p className="warning" key={issue}>{issue}</p>)}
              </fieldset>

              <fieldset className="step"><legend>3 · Map columns to process variables</legend>
                <p className="muted">Tick each column to import and confirm its variable name and unit. Suggestions come from tag-name patterns and must be checked.</p>
                <div className="hierarchy-row">
                  <label>Process unit (optional)<select value={unitId} onChange={(event) => { setUnitId(event.target.value); setEquipmentId(""); }}><option value="">None</option>{(hierarchy.data?.process_units ?? []).map((unit) => <option key={unit.id} value={unit.id}>{unit.name} · {unit.unit_type}</option>)}</select></label>
                  <label>Equipment (optional)<select value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)}><option value="">None</option>{availableEquipment.map((item) => <option key={item.id} value={item.id}>{item.tag} · {item.name}</option>)}</select></label>
                </div>
                {rows.length === 0 ? <div className="empty-state">No numeric columns were found to map. Check that values are plain numbers.</div> : (
                  <div className="table-wrap"><table>
                    <thead><tr><th>Import</th><th>Column</th><th>Variable name</th><th>Unit</th></tr></thead>
                    <tbody>{rows.map((row, index) => (
                      <tr key={row.source_tag}>
                        <td><input type="checkbox" aria-label={`Import ${row.source_tag}`} checked={row.include} onChange={(event) => setRows(rows.map((item, i) => i === index ? { ...item, include: event.target.checked } : item))} /></td>
                        <td><strong>{row.source_tag}</strong>{row.suggested && <small>suggested from the tag name</small>}</td>
                        <td><input type="text" value={row.canonical_name} placeholder="e.g. reactor.temperature" disabled={!row.include} onChange={(event) => setRows(rows.map((item, i) => i === index ? { ...item, canonical_name: event.target.value } : item))} /></td>
                        <td><select value={row.unit} disabled={!row.include} onChange={(event) => setRows(rows.map((item, i) => i === index ? { ...item, unit: event.target.value } : item))}><option value="">Select unit</option>{unitOptions.map((unit) => <option key={unit} value={unit}>{unit} ({preview.supported_units[unit]})</option>)}</select></td>
                      </tr>
                    ))}</tbody>
                  </table></div>
                )}
                {rowProblems.length > 0 && <p className="warning">Enter a variable name and unit for: {rowProblems.map((row) => row.source_tag).join(", ")}.</p>}
              </fieldset>

              <fieldset className="step"><legend>4 · Validation preview</legend>
                {preview.blocking_errors.length > 0 && <div className="error-state" role="alert">{preview.blocking_errors.map((text) => <p key={text}>{text}</p>)}</div>}
                {!preview.validation && preview.blocking_errors.length === 0 && <p className="muted">Select at least one column to see how its values would be classified.</p>}
                {preview.validation && (
                  <>
                    <div className="kpi-row compact">
                      {["GOOD", "SUSPECT", "BAD", "MISSING"].map((status) => <article className="kpi" key={status}><p>{status}</p><div className="kpi-value kpi-number">{preview.validation?.status_counts[status] ?? 0}</div></article>)}
                      <article className="kpi"><p>Usable</p><div className="kpi-value kpi-number">{formatNumber(preview.validation.usable_fraction * 100, 0)}%</div></article>
                    </div>
                    {Object.keys(preview.validation.reason_counts).length > 0 && <p className="muted">Reasons: {Object.entries(preview.validation.reason_counts).map(([reason, count]) => `${reason.replaceAll("_", " ").toLowerCase()} ×${count}`).join(" · ")}</p>}
                    {preview.validation.warning && <p className="warning">{preview.validation.warning}</p>}
                  </>
                )}
                <details><summary>First rows of the file</summary><div className="table-wrap"><table><thead><tr>{preview.columns.map((column) => <th key={column.name}>{column.name}</th>)}</tr></thead><tbody>{preview.sample_rows.map((row, index) => <tr key={index}>{preview.columns.map((column) => <td key={column.name}>{row[column.name]}</td>)}</tr>)}</tbody></table></div></details>
              </fieldset>

              <fieldset className="step"><legend>5 · Import</legend>
                <label>Dataset name<input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} required maxLength={200} /></label>
                <button type="submit" disabled={!canImport}>{busy === "importing" ? "Importing…" : "Import dataset"}</button>
                {!canImport && busy === "" && <small className="muted">Complete the steps above (timestamp column, at least one mapped column with name and unit, dataset name) to enable import.</small>}
              </fieldset>
            </>
          )}
        </form>
      </section>

      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">STORED DATASETS</p><h2>Dataset versions</h2></div><button type="button" className="secondary" onClick={() => void datasets.reload()}>Refresh</button></div>
        {datasets.error && <div className="error-state" role="alert">{datasets.error}</div>}
        {(datasets.data?.items ?? []).length === 0 ? <div className="empty-state">No historical datasets have been imported for this organization.</div> : (
          <div className="dataset-list">
            {(datasets.data?.items ?? []).map((dataset) => (
              <button className="dataset-row" key={dataset.id} onClick={() => dataset.latest_version && void explore(dataset.latest_version.id)} disabled={!dataset.latest_version}>
                <span><strong>{dataset.name}</strong><small>{dataset.latest_version?.source_filename ?? "No version"}</small></span>
                <span>{dataset.latest_version ? `v${dataset.latest_version.version} · ${dataset.latest_version.row_count} rows` : "Empty"}</span>
              </button>
            ))}
          </div>
        )}
      </section>
      {exploration && <ExplorationView exploration={exploration} start={start} end={end} setStart={setStart} setEnd={setEnd} onApply={() => void explore(exploration.dataset_version.id)} />}
    </div>
  );
}

function ExplorationView({ exploration, start, end, setStart, setEnd, onApply }: { exploration: Exploration; start: string; end: string; setStart: (value: string) => void; setEnd: (value: string) => void; onApply: () => void }) {
  const chartVariables = Object.entries(exploration.trends).slice(0, 3);
  return (
    <section className="panel exploration">
      <div className="panel-title"><div><p className="eyebrow">DATASET VERSION {exploration.dataset_version.version} · HISTORICAL</p><h2>Quality and trends</h2></div><span className="pill">{exploration.variables.length} variables</span></div>
      <div className="controls"><label>Start<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label><label>End<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} /></label><button type="button" onClick={onApply}>Apply time range</button></div>
      <div className="trend-grid">{chartVariables.map(([name, points]) => (
        <article className="trend-card" key={name}><h3>{name}</h3>
          <ResponsiveContainer width="100%" height={180}><LineChart data={points}><CartesianGrid stroke="#17384e" strokeDasharray="3 3" /><XAxis dataKey="timestamp" hide /><YAxis width={48} tick={{ fill: "#86a0ae", fontSize: 10 }} domain={["auto", "auto"]} /><Tooltip labelFormatter={(label) => new Date(String(label)).toLocaleString()} /><Line dataKey="value" stroke="#60d5c6" dot={false} strokeWidth={2} isAnimationActive={false} /></LineChart></ResponsiveContainer>
        </article>
      ))}</div>
      <div className="table-wrap"><table>
        <thead><tr>{["Variable", "Samples / missing", "Min / max", "Mean ± σ", "p05 / p50 / p95", "Rate", "Quality"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{exploration.variables.map((variable) => (
          <tr key={variable.canonical_name}>
            <td><strong>{variable.canonical_name}</strong><small>{variable.source_tags.join(", ")}</small></td>
            <td>{variable.sample_count} / {variable.missing_count}<small>{variable.unavailable_count} unusable</small></td>
            <td>{display(variable.min)} / {display(variable.max)} {variable.normalized_unit}</td>
            <td>{display(variable.mean)} ± {display(variable.standard_deviation)}</td>
            <td>{display(variable.percentiles.p05)} / {display(variable.percentiles.p50)} / {display(variable.percentiles.p95)}</td>
            <td>{display(variable.sampling_rate_hz, 4)} Hz</td>
            <td>{Object.entries(variable.quality_counts).map(([quality, count]) => `${quality}: ${count}`).join(" · ")}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <div className="grid bottom">
        <article><h3>Data gaps</h3>{exploration.data_gaps.length ? exploration.data_gaps.slice(0, 100).map((gap, index) => <p key={`${gap.variable}-${index}`} className="muted">{gap.variable}: {gap.start} → {gap.end} ({Math.round(gap.duration_s)} s)</p>) : <p className="muted">No communication gaps detected in the selected range.</p>}</article>
        <article><h3>Correlation</h3><pre className="data-json">{JSON.stringify(exploration.correlation, null, 2)}</pre></article>
      </div>
    </section>
  );
}
