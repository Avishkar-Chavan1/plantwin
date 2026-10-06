"use client";

import { useEffect, useState } from "react";
import { useDashboardData } from "@/components/use-dashboard-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Model = { id: string; name: string; version: number; type: string; status: string; operating_envelope: Record<string, unknown> | null };
type Result = { baseline: Record<string, number>; optimized: Record<string, number> & { variables: { temperature_c: number; pressure_bar: number; flow_m3_h: number } }; objective_improvement: number; energy_impact_kw: number; constraints: { status: string }; advisory: string; model_validity: string };
function number(value: number | undefined) { return value === undefined ? "—" : value.toFixed(2); }

export default function Optimization() {
  const { summary, headers, isLoading } = useDashboardData();
  const equipment = (summary as { equipment?: { id: string; tag: string } } | null)?.equipment;
  const [models, setModels] = useState<Model[]>([]);
  const [modelId, setModelId] = useState("");
  const [inputs, setInputs] = useState({ temperature_c: 180, pressure_bar: 10, flow_m3_h: 72, energy_weight: 0.02 });
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!headers) return;
    void fetch(`${apiUrl}/api/v1/models`, { headers }).then(async (response) => { if (!response.ok) throw new Error("Unable to load model registry."); const items = (await response.json() as { items?: Model[] }).items ?? []; const usable = items.filter((item) => ["VALIDATED", "STAGING", "PRODUCTION"].includes(item.status) && item.type === "physics_cstr"); setModels(usable); setModelId((current) => current || usable[0]?.id || ""); }).catch((error) => setMessage(error.message));
  }, [headers]);
  async function runOptimization() {
    if (!equipment || !headers || !modelId) { setMessage("A tenant-owned VALIDATED or PRODUCTION CSTR model is required before optimization."); return; }
    setBusy(true); setMessage("Running bounded optimization within the validated operating envelope…");
    try { const response = await fetch(`${apiUrl}/api/v1/optimization/runs`, { method: "POST", headers, body: JSON.stringify({ equipment_id: equipment.id, model_version_id: modelId, ...inputs }) }); const body = await response.json(); if (!response.ok) { setMessage(body.detail?.message ?? body.error?.message ?? "Optimization failed."); return; } setResult(body as Result); setMessage("Optimization completed as an advisory model run. No plant setting was changed."); } catch (error) { setMessage(error instanceof Error ? error.message : "Optimization failed."); } finally { setBusy(false); }
  }
  if (isLoading) return <div className="console-loading">Loading optimization inputs…</div>;
  if (!equipment) return <section className="panel empty-state"><h2>No equipment configured</h2><p>Configure equipment before requesting an optimization run.</p></section>;
  return <div className="optimization-view"><p className="notice">Optimization is available only against a tenant-owned validated CSTR model and its operating envelope. Results are advisory and read-only.</p><p className="message" role="status">{message}</p><section className="grid"><article className="panel"><p className="eyebrow">BOUNDED OPTIMIZATION · {equipment.tag}</p><h2>Objective and baseline</h2>{models.length ? <label>Validated model<select value={modelId} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.name} v{model.version} · {model.status}</option>)}</select></label> : <div className="empty-state">No validated CSTR model is available. Register and independently validate one in Models before optimizing.</div>}<div className="controls"><label>Temperature °C<input type="number" min={170} max={190} value={inputs.temperature_c} onChange={(event) => setInputs({ ...inputs, temperature_c: Number(event.target.value) })} /></label><label>Pressure bar<input type="number" min={8} max={12} value={inputs.pressure_bar} onChange={(event) => setInputs({ ...inputs, pressure_bar: Number(event.target.value) })} /></label><label>Flow m³/h<input type="number" min={57.6} max={86.4} value={inputs.flow_m3_h} onChange={(event) => setInputs({ ...inputs, flow_m3_h: Number(event.target.value) })} /></label><label>Energy weight<input type="number" min={0} max={1} step="0.01" value={inputs.energy_weight} onChange={(event) => setInputs({ ...inputs, energy_weight: Number(event.target.value) })} /></label></div><button onClick={() => void runOptimization()} disabled={busy || !models.length}>{busy ? "Optimizing…" : "Run bounded optimization"}</button></article><article className="panel"><p className="eyebrow">ADVISORY OUTPUT</p><h2>{result ? "Recommended operating point" : "Awaiting a run"}</h2>{result ? <><div className="metrics compact-metrics"><Metric label="Yield" value={number(result.optimized.yield_pct)} unit="%" /><Metric label="Conversion" value={number(result.optimized.conversion_pct)} unit="%" /><Metric label="Energy" value={number(result.optimized.energy_proxy_kw)} unit="kW" /></div><div className="impact"><span>Temperature<b>{number(result.optimized.variables.temperature_c)}°C</b></span><span>Pressure<b>{number(result.optimized.variables.pressure_bar)} bar</b></span><span>Flow<b>{number(result.optimized.variables.flow_m3_h)} m³/h</b></span></div><span className="pill good">CONSTRAINTS {result.constraints.status}</span><p className="muted">Objective improvement {number(result.objective_improvement)} · energy impact {number(result.energy_impact_kw)} kW</p><p>{result.advisory}</p></> : <div className="empty-state">The API will return baseline, optimized metrics, bounds, and advisory text after a valid model run.</div>}</article></section></div>;
}
function Metric({ label, value, unit }: { label: string; value: string; unit: string }) { return <article className="metric"><p>{label}</p><h2>{value}<small>{unit}</small></h2></article>; }
