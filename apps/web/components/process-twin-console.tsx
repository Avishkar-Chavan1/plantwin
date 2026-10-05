"use client";

import { FormEvent, useEffect, useMemo, useState, useCallback } from "react";
import { HistoricalDataExplorer } from "./historical-data-explorer";
import { LiveDataSources } from "./live-data-sources";
import { ModelGovernance } from "./model-governance";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Reading = { value: number; unit: string; quality_status: string; source: string; timestamp: string };
type Summary = {
  source_mode?: "SIMULATION" | "HISTORICAL" | "LIVE_READ_ONLY" | "MIXED_DATA_BLOCKED" | "NO_DATA";
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

interface ProcessTwinConsoleProps {
  initialView: string;
  token: string;
  organization: string;
  headers: Record<string, string>;
  summary: any;
  plants: any[];
  setMessage: (msg: string) => void;
  isLoading: boolean;
}

function number(value?: number, digits = 1) { return value === undefined ? "—" : value.toFixed(digits); }

export function ProcessTwinConsole({ initialView, token, organization, headers, summary, plants, setMessage, isLoading }: ProcessTwinConsoleProps) {
  const [scenario, setScenario] = useState({ temperature_c: 185, pressure_bar: 10, flow_m3_h: 72 });
  const [simulation, setSimulation] = useState<any>(null);
  const [optimization, setOptimization] = useState<any>(null);
  const [exploration, setExploration] = useState<any>(null);
  const [message, setMessageState] = useState("");
  const [messageState, setMessageStateInternal] = useState("");

  // Sync message from parent
  useEffect(() => {
    setMessageStateInternal(message);
  }, [message]);

  const loadSummary = useCallback(async () => {
    if (!headers) return;
    try {
      const response = await fetch(`${apiUrl}/api/v1/dashboard/summary`, { headers });
      if (response.ok) {
        // Summary is managed by parent layout
      }
    } catch {
      // ignore
    }
  }, [headers]);

  async function runSimulation() {
    if (!headers || !summary?.equipment) return;
    const response = await fetch(`${apiUrl}/api/v1/simulations`, {
      method: "POST",
      headers,
      body: JSON.stringify({ equipment_id: summary.equipment.id, ...scenario }),
    });
    const data = await response.json();
    if (!response.ok) {
      setMessageStateInternal(data.error?.message ?? "Simulation failed");
      return;
    }
    setSimulation(data);
    setMessageStateInternal("Scenario simulated using the CSTR physics model. No plant setting was changed.");
  }

  async function runOptimization() {
    if (!headers || !summary?.equipment) return;
    const response = await fetch(`${apiUrl}/api/v1/optimization/runs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ equipment_id: summary.equipment.id, ...scenario }),
    });
    const data = await response.json();
    if (!response.ok) {
      setMessageStateInternal(data.error?.message ?? "Optimization failed");
      return;
    }
    setOptimization(data);
    setMessageStateInternal("An advisory bounded optimization has been generated.");
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
    if (!response.ok) {
      setMessageStateInternal(data.error?.message ?? "Historical import failed");
      return;
    }
    const result = await fetch(`${apiUrl}/api/v1/datasets/${data.version.id}/exploration`, { headers });
    if (!result.ok) {
      setMessageStateInternal("Import succeeded, but exploration could not be loaded.");
      return;
    }
    setExploration(await result.json());
    setMessageStateInternal(`Imported ${data.version.row_count} historical rows as dataset version ${data.version.version}.`);
  }

  if (isLoading) {
    return <div className="console-loading">Loading...</div>;
  }

  if (initialView === "data") return <HistoricalDataExplorer token={token} organization={organization} />;
  if (initialView === "data-sources") return <LiveDataSources token={token} organization={organization} />;
  if (initialView === "models") return <ModelGovernance token={token} organization={organization} />;

  const m = summary?.measurements ?? {};
  const currentView = initialView;

  return (
    <>
      <p className="notice">{summary?.safety_notice}</p>
      <p className="message">{messageState}</p>
      <div className="metrics">
        <Metric label="Reactor temperature" value={number(m.REACTOR_TEMPERATURE?.value)} unit={m.REACTOR_TEMPERATURE?.unit ?? "°C"} status={m.REACTOR_TEMPERATURE?.source} />
        <Metric label="Reactor pressure" value={number(m.REACTOR_PRESSURE?.value, 2)} unit={m.REACTOR_PRESSURE?.unit ?? "bar"} status={m.REACTOR_PRESSURE?.source} />
        <Metric label="Feed flow" value={number(m.FEED_FLOW?.value)} unit={m.FEED_FLOW?.unit ?? "m³/h"} status={m.FEED_FLOW?.source} />
        <Metric label="Predicted yield" value={number(summary?.twin?.yield?.value)} unit="%" status="ESTIMATED" />
      </div>
      <section className="grid">
        <article className="panel trend">
          <div className="panel-title">
            <div>
              <p className="eyebrow">DIGITAL TWIN / R-101</p>
              <h2>Process behavior</h2>
            </div>
            <span className="pill">Last 24 hours</span>
          </div>
          <div className="plot">
            <svg viewBox="0 0 700 220" role="img" aria-label="Illustrative process trend">
              <path d="M0 158 C45 150 60 106 115 120 S175 179 225 118 S310 80 350 115 S420 155 475 78 S560 45 610 108 S655 136 700 82" fill="none" stroke="#60d5c6" strokeWidth="4" />
              <path d="M0 185 C80 170 132 188 180 154 S285 175 330 149 S435 171 510 142 S610 160 700 130" fill="none" stroke="#f2b36d" strokeWidth="3" strokeDasharray="7 8" />
              <line x1="0" y1="210" x2="700" y2="210" stroke="#25445a" />
            </svg>
            <div className="legend">
              <span><i className="line mint" />Temperature</span>
              <span><i className="line amber" />Yield</span>
            </div>
          </div>
        </article>
        <article className="panel reactor">
          <p className="eyebrow">LIVE PROCESS SCHEMATIC</p>
          <h2>CSTR R-101</h2>
          <div className="process">
            <span className="feed">FEED A</span>
            <span className="pipe" />
            <div className="vessel">
              <b>R-101</b>
              <small>stirred reactor</small>
              <i>↻</i>
            </div>
            <span className="jacket">COOLING<br />JACKET</span>
            <span className="product">PRODUCT B</span>
          </div>
          <div className="mini">
            <span>Conversion <b>{number(summary?.twin?.conversion?.value)}%</b></span>
            <span>Selectivity <b>{number(summary?.twin?.selectivity?.value)}%</b></span>
          </div>
        </article>
      </section>
      <section className="grid bottom">
        <article className="panel scenario">
          <p className="eyebrow">WHAT-IF SCENARIO</p>
          <h2>Test operating conditions</h2>
          <div className="controls">
            <label>Temperature °C<input type="number" value={scenario.temperature_c} onChange={(e) => setScenario({...scenario, temperature_c: Number(e.target.value)})} /></label>
            <label>Pressure bar<input type="number" value={scenario.pressure_bar} onChange={(e) => setScenario({...scenario, pressure_bar: Number(e.target.value)})} /></label>
            <label>Flow m³/h<input type="number" value={scenario.flow_m3_h} onChange={(e) => setScenario({...scenario, flow_m3_h: Number(e.target.value)})} /></label>
          </div>
          <div className="actions">
            <button onClick={runSimulation}>Simulate</button>
            <button className="secondary" onClick={runOptimization}>Optimize</button>
          </div>
          {simulation && (
            <div className="result">
              <b>Scenario yield {number(simulation.scenario?.yield_pct)}%</b>
              <span>
                {simulation.difference?.yield_percentage_points >= 0 ? "+" : ""}
                {number(simulation.difference?.yield_percentage_points)} pp yield · {number(simulation.scenario?.energy_proxy_kw)} kW energy proxy
              </span>
              {simulation.constraint_violations?.map((item: string) => <em key={item}>{item}</em>)}
            </div>
          )}
        </article>
        <article className="panel recommendation">
          <p className="eyebrow">ADVISORY RECOMMENDATION</p>
          <h2>{optimization ? "Recommended operating window" : "Awaiting optimization"}</h2>
          {optimization ? (
            <>
              <p>Move toward <b>{number(optimization.optimized?.variables?.temperature_c)}°C</b>, <b>{number(optimization.optimized?.variables?.flow_m3_h)} m³/h</b>.</p>
              <div className="impact">
                <span>Expected yield <b>{number(optimization.optimized?.yield_pct)}%</b></span>
                <span>Energy proxy <b>{number(optimization.optimized?.energy_kw)} kW</b></span>
                <span>Constraints <b className="pass">{optimization.constraints?.status}</b></span>
              </div>
              <small>{optimization.advisory}</small>
            </>
          ) : (
            <>
              <p>Run a bounded optimization to generate a physics-backed advisory scenario.</p>
              <small>Recommendations never modify plant controls directly.</small>
            </>
          )}
        </article>
      </section>
    </>
  );
}

function Metric({ label, value, unit, status }: { label: string; value: string; unit: string; status?: string }) {
  return <article className="metric"><p>{label}</p><h2>{value}<small>{unit}</small></h2><span>{status ?? "ESTIMATED"}</span></article>;
}