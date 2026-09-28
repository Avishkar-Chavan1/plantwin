"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { HistoricalDataExplorer } from "./historical-data-explorer";

type Reading = { value: number; unit: string; quality_status: string; source: string; timestamp: string };
type Summary = {
  equipment?: { id: string; tag: string; name: string };
  plant_health?: string;
  measurements?: Record<string, Reading>;
  twin?: { conversion: Reading; yield: Reading; selectivity: Reading; heat_removal: Reading; temperature: Reading; divergence_temperature_k?: number };
  active_alerts?: number;
  safety_notice?: string;
};
type Simulation = { scenario: { yield_pct: number; conversion_pct: number; energy_proxy_kw: number }; difference: { yield_percentage_points: number; energy_kw: number }; constraint_violations: string[] };
type Optimization = { optimized: { yield_pct: number; energy_kw: number; variables: { temperature_c: number; pressure_bar: number; flow_m3_h: number } }; constraints: { status: string }; advisory: string };
type PlantOption = { id: string; name: string };
type DatasetExploration = { dataset_version: { id: string; version: number }; variables: Array<{ canonical_name: string; engineering_unit: string; normalized_unit: string; sample_count: number; missing_count: number; min: number | null; max: number | null; mean: number | null; standard_deviation: number | null; percentiles: Record<string, number | null>; sampling_rate_hz: number | null; quality_counts: Record<string, number> }>; data_gaps: Array<{ variable: string; start: string; end: string; duration_s: number }> };

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const navigation = ["dashboard", "plants", "equipment", "sensors", "digital-twins", "simulations", "optimization", "recommendations", "alerts", "models", "data", "settings", "audit-log"];

function number(value?: number, digits = 1) { return value === undefined ? "—" : value.toFixed(digits); }

export function ProcessTwinConsole({ initialView }: { initialView: string }) {
  const [token, setToken] = useState<string | null>(null);
  const [organization, setOrganization] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [message, setMessage] = useState("Sign in with the documented demo account to inspect simulated CSTR R-101 data.");
  const [scenario, setScenario] = useState({ temperature_c: 185, pressure_bar: 10, flow_m3_h: 72 });
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [optimization, setOptimization] = useState<Optimization | null>(null);
  const [plants, setPlants] = useState<PlantOption[]>([]);
  const [exploration, setExploration] = useState<DatasetExploration | null>(null);

  const headers = useMemo(() => token && organization ? { Authorization: `Bearer ${token}`, "X-Organization-ID": organization, "Content-Type": "application/json" } : undefined, [token, organization]);
  async function loadSummary() {
    if (!headers) return;
    const response = await fetch(`${apiUrl}/api/v1/dashboard/summary`, { headers });
    if (!response.ok) { setMessage("Unable to load the tenant-scoped dashboard."); return; }
    setSummary(await response.json());
  }
  useEffect(() => { void loadSummary(); }, [headers]);
  useEffect(() => {
    if (!headers) return;
    void fetch(`${apiUrl}/api/v1/plants`, { headers }).then(async (response) => {
      if (response.ok) setPlants((await response.json()).items);
    });
  }, [headers]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const response = await fetch(`${apiUrl}/api/v1/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
    if (!response.ok) { setMessage("Sign-in failed. Confirm that demo data has been seeded."); return; }
    const data = await response.json();
    setToken(data.access_token); setOrganization(data.organizations[0]?.id ?? null); setMessage("Connected to the simulated demonstration tenant.");
  }
  async function runSimulation() {
    if (!headers || !summary?.equipment) return;
    const response = await fetch(`${apiUrl}/api/v1/simulations`, { method: "POST", headers, body: JSON.stringify({ equipment_id: summary.equipment.id, ...scenario }) });
    const data = await response.json();
    if (!response.ok) { setMessage(data.error?.message ?? "Simulation failed"); return; }
    setSimulation(data); setMessage("Scenario simulated using the CSTR physics model. No plant setting was changed.");
  }
  async function runOptimization() {
    if (!headers || !summary?.equipment) return;
    const response = await fetch(`${apiUrl}/api/v1/optimization/runs`, { method: "POST", headers, body: JSON.stringify({ equipment_id: summary.equipment.id, ...scenario }) });
    const data = await response.json();
    if (!response.ok) { setMessage(data.error?.message ?? "Optimization failed"); return; }
    setOptimization(data); setMessage("An advisory bounded optimization has been generated.");
  }

  async function importHistoricalData(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !organization) return;
    const form = new FormData(event.currentTarget);
    const response = await fetch(`${apiUrl}/api/v1/datasets/import`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "X-Organization-ID": organization },
      body: form,
    });
    const data = await response.json();
    if (!response.ok) { setMessage(data.error?.message ?? "Historical import failed"); return; }
    const result = await fetch(`${apiUrl}/api/v1/datasets/${data.version.id}/exploration`, { headers });
    if (!result.ok) { setMessage("Import succeeded, but exploration could not be loaded."); return; }
    setExploration(await result.json());
    setMessage(`Imported ${data.version.row_count} historical rows as dataset version ${data.version.version}.`);
  }

  if (!token || initialView === "login") return <main className="login"><section className="brand"><p className="eyebrow">PROCESS TWIN / REFERENCE PLANT</p><h1>Physics-informed<br />industrial intelligence.</h1><p>Monitor, simulate and optimize—without sending control commands to the plant.</p></section><form className="login-card" onSubmit={login}><h2>Welcome back</h2><label>Email<input name="email" type="email" defaultValue="engineer@processtwin.demo" required /></label><label>Password<input name="password" type="password" defaultValue="ChangeMeDemoOnly!" required /></label><button type="submit">Sign in to demo</button><small>{message}</small></form></main>;

  if (initialView === "data") return <HistoricalDataExplorer token={token} organization={organization!} />;

  const m = summary?.measurements ?? {};
  return <main className="shell"><aside><div className="logo"><span>◈</span> ProcessTwin</div><p className="tenant">SIMULATION ENVIRONMENT</p><nav>{navigation.map((entry) => <a className={initialView === entry ? "active" : ""} href={`/${entry === "dashboard" ? "dashboard" : entry}`} key={entry}>{entry.replaceAll("-", " ")}</a>)}</nav><div className="operator"><span className="dot" /> Human-in-the-loop<br /><small>No control connection</small></div></aside><section className="workspace"><header><div><p className="eyebrow">DEMO CHEMICAL PLANT / {summary?.equipment?.tag ?? "—"}</p><h1>{initialView.replaceAll("-", " ")}</h1></div><div className="header-status"><span className="pill good">{summary?.plant_health ?? "LOADING"}</span><span>● SIMULATED DATA</span></div></header><p className="notice">{summary?.safety_notice}</p><p className="message">{message}</p>
    <div className="metrics"><Metric label="Reactor temperature" value={number(m.REACTOR_TEMPERATURE?.value)} unit={m.REACTOR_TEMPERATURE?.unit ?? "°C"} status={m.REACTOR_TEMPERATURE?.source} /><Metric label="Reactor pressure" value={number(m.REACTOR_PRESSURE?.value, 2)} unit={m.REACTOR_PRESSURE?.unit ?? "bar"} status={m.REACTOR_PRESSURE?.source} /><Metric label="Feed flow" value={number(m.FEED_FLOW?.value)} unit={m.FEED_FLOW?.unit ?? "m³/h"} status={m.FEED_FLOW?.source} /><Metric label="Predicted yield" value={number(summary?.twin?.yield.value)} unit="%" status="ESTIMATED" /></div>
    <section className="grid"><article className="panel trend"><div className="panel-title"><div><p className="eyebrow">DIGITAL TWIN / R-101</p><h2>Process behavior</h2></div><span className="pill">Last 24 hours</span></div><div className="plot"><svg viewBox="0 0 700 220" role="img" aria-label="Illustrative process trend"><path d="M0 158 C45 150 60 106 115 120 S175 179 225 118 S310 80 350 115 S420 155 475 78 S560 45 610 108 S655 136 700 82" fill="none" stroke="#60d5c6" strokeWidth="4"/><path d="M0 185 C80 170 132 188 180 154 S285 175 330 149 S435 171 510 142 S610 160 700 130" fill="none" stroke="#f2b36d" strokeWidth="3" strokeDasharray="7 8"/><line x1="0" y1="210" x2="700" y2="210" stroke="#25445a"/></svg><div className="legend"><span><i className="line mint" />Temperature</span><span><i className="line amber" />Yield</span></div></div></article><article className="panel reactor"><p className="eyebrow">LIVE PROCESS SCHEMATIC</p><h2>CSTR R-101</h2><div className="process"><span className="feed">FEED A</span><span className="pipe" /><div className="vessel"><b>R-101</b><small>stirred reactor</small><i>↻</i></div><span className="jacket">COOLING<br />JACKET</span><span className="product">PRODUCT B</span></div><div className="mini"><span>Conversion <b>{number(summary?.twin?.conversion.value)}%</b></span><span>Selectivity <b>{number(summary?.twin?.selectivity.value)}%</b></span></div></article></section>
    <section className="grid bottom"><article className="panel scenario"><p className="eyebrow">WHAT-IF SCENARIO</p><h2>Test operating conditions</h2><div className="controls"><label>Temperature °C<input type="number" value={scenario.temperature_c} onChange={(e) => setScenario({...scenario, temperature_c: Number(e.target.value)})} /></label><label>Pressure bar<input type="number" value={scenario.pressure_bar} onChange={(e) => setScenario({...scenario, pressure_bar: Number(e.target.value)})} /></label><label>Flow m³/h<input type="number" value={scenario.flow_m3_h} onChange={(e) => setScenario({...scenario, flow_m3_h: Number(e.target.value)})} /></label></div><div className="actions"><button onClick={runSimulation}>Simulate</button><button className="secondary" onClick={runOptimization}>Optimize</button></div>{simulation && <div className="result"><b>Scenario yield {number(simulation.scenario.yield_pct)}%</b><span>{simulation.difference.yield_percentage_points >= 0 ? "+" : ""}{number(simulation.difference.yield_percentage_points)} pp yield · {number(simulation.scenario.energy_proxy_kw)} kW energy proxy</span>{simulation.constraint_violations.map((item) => <em key={item}>{item}</em>)}</div>}</article><article className="panel recommendation"><p className="eyebrow">ADVISORY RECOMMENDATION</p><h2>{optimization ? "Recommended operating window" : "Awaiting optimization"}</h2>{optimization ? <><p>Move toward <b>{number(optimization.optimized.variables.temperature_c)}°C</b>, <b>{number(optimization.optimized.variables.flow_m3_h)} m³/h</b>.</p><div className="impact"><span>Expected yield <b>{number(optimization.optimized.yield_pct)}%</b></span><span>Energy proxy <b>{number(optimization.optimized.energy_kw)} kW</b></span><span>Constraints <b className="pass">{optimization.constraints.status}</b></span></div><small>{optimization.advisory}</small></> : <><p>Run a bounded optimization to generate a physics-backed advisory scenario.</p><small>Recommendations never modify plant controls.</small></>}</article></section>
  </section></main>;
}

function Metric({ label, value, unit, status }: { label: string; value: string; unit: string; status?: string }) { return <article className="metric"><p>{label}</p><h2>{value}<small>{unit}</small></h2><span>{status ?? "ESTIMATED"}</span></article>; }
