"use client";

import { useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useDashboardData } from "@/components/use-dashboard-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Simulation = { baseline: Record<string, number>; scenario: Record<string, number>; difference: Record<string, number>; trajectory: Array<{ time_s: number; temperature_c: number; conversion_pct: number; yield_pct: number; energy_proxy_kw: number }>; constraint_violations: string[]; model_validity: string; warnings: string[]; advisory: string };
function number(value: number | undefined, digits = 2) { return value === undefined ? "—" : value.toFixed(digits); }

export default function Simulations() {
  const { summary, headers, isLoading } = useDashboardData();
  const equipment = (summary as { equipment?: { id: string; tag: string } } | null)?.equipment;
  const [scenario, setScenario] = useState({ temperature_c: 185, pressure_bar: 10, flow_m3_h: 72 });
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function runSimulation() {
    if (!equipment || !headers) { setMessage("No equipment is available for simulation."); return; }
    if (![scenario.temperature_c, scenario.pressure_bar, scenario.flow_m3_h].every(Number.isFinite)) { setMessage("Enter finite numeric operating conditions."); return; }
    setBusy(true); setMessage("Running the CSTR physics model…");
    try {
      const response = await fetch(`${apiUrl}/api/v1/simulations`, { method: "POST", headers, body: JSON.stringify({ equipment_id: equipment.id, ...scenario }) });
      const data = await response.json();
      if (!response.ok) { setMessage(data.detail?.message ?? data.error?.message ?? "Simulation failed."); return; }
      setSimulation(data as Simulation); setMessage("Simulation completed. This response is advisory only; no plant setting was changed.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Network error during simulation."); }
    finally { setBusy(false); }
  }
  if (isLoading) return <div className="console-loading">Loading simulation inputs…</div>;
  if (!equipment) return <section className="panel empty-state"><h2>No equipment configured</h2><p>Configure equipment before running a CSTR scenario.</p></section>;
  return <div className="simulations-view"><p className="notice">What-if scenarios call the tenant’s CSTR model and persist the request/result for auditability. They do not connect to or command a plant.</p><p className="message" role="status">{message}</p><section className="grid"><article className="panel scenario"><p className="eyebrow">CSTR SCENARIO · {equipment.tag}</p><h2>Operating conditions</h2><div className="controls"><label>Temperature °C<input type="number" min={-50} max={500} step="any" value={scenario.temperature_c} onChange={(event) => setScenario({ ...scenario, temperature_c: Number(event.target.value) })} disabled={busy} /></label><label>Pressure bar<input type="number" min={0.01} max={500} step="any" value={scenario.pressure_bar} onChange={(event) => setScenario({ ...scenario, pressure_bar: Number(event.target.value) })} disabled={busy} /></label><label>Flow m³/h<input type="number" min={0.01} max={10000} step="any" value={scenario.flow_m3_h} onChange={(event) => setScenario({ ...scenario, flow_m3_h: Number(event.target.value) })} disabled={busy} /></label></div><button onClick={() => void runSimulation()} disabled={busy}>{busy ? "Simulating…" : "Run simulation"}</button></article><article className="panel"><p className="eyebrow">MODEL RESPONSE</p><h2>{simulation ? "Scenario metrics" : "Awaiting a run"}</h2>{simulation ? <><div className="metrics compact-metrics"><Metric label="Yield" value={number(simulation.scenario.yield_pct)} unit="%" /><Metric label="Conversion" value={number(simulation.scenario.conversion_pct)} unit="%" /><Metric label="Energy proxy" value={number(simulation.scenario.energy_proxy_kw)} unit="kW" /></div><p className="muted">Δ yield {simulation.difference.yield_percentage_points >= 0 ? "+" : ""}{number(simulation.difference.yield_percentage_points)} pp · Δ energy {number(simulation.difference.energy_kw)} kW</p><span className="pill">{simulation.model_validity}</span>{simulation.constraint_violations.map((item) => <p className="warning" key={item}>{item}</p>)}</> : <div className="empty-state">Change an input and run the model to receive returned metrics and a trajectory.</div>}</article></section>{simulation && <section className="panel"><div className="panel-title"><div><p className="eyebrow">RETURNED TRAJECTORY</p><h2>Model response over time</h2></div><span className="pill">{simulation.trajectory.length} samples</span></div><div className="plot"><ResponsiveContainer width="100%" height={320}><LineChart data={simulation.trajectory}><XAxis dataKey="time_s" tick={{ fill: "#86a0ae", fontSize: 11 }} tickFormatter={(value) => `${Number(value).toFixed(0)}s`} /><YAxis yAxisId="left" tick={{ fill: "#86a0ae", fontSize: 11 }} /><YAxis yAxisId="right" orientation="right" tick={{ fill: "#86a0ae", fontSize: 11 }} /><Tooltip labelFormatter={(label) => `t = ${label}s`} /><Line yAxisId="left" type="monotone" dataKey="temperature_c" stroke="#60d5c6" dot={false} name="Temperature °C" /><Line yAxisId="right" type="monotone" dataKey="yield_pct" stroke="#f2b36d" dot={false} name="Yield %" /></LineChart></ResponsiveContainer></div>{simulation.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}<p className="muted">{simulation.advisory}</p></section>}</div>;
}

function Metric({ label, value, unit }: { label: string; value: string; unit: string }) { return <article className="metric"><p>{label}</p><h2>{value}<small>{unit}</small></h2></article>; }
