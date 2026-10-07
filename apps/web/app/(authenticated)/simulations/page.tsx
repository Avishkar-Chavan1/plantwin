"use client";

import { useCallback, useEffect, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useWorkspaceData } from "@/components/workspace-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Simulation = {
  baseline: Record<string, number>;
  scenario: Record<string, number>;
  difference: Record<string, number>;
  trajectory: Array<{ time_s: number; temperature_c: number; conversion_pct: number; yield_pct: number; energy_proxy_kw: number }>;
  constraint_violations: string[];
  model_validity: string;
  warnings: string[];
  advisory: string;
};

type ModelOption = { id: string; name: string; version: number; status: string };

const SCENARIO_LIMITS = {
  temperature_c: { min: 100, max: 300 },
  pressure_bar: { min: 0.1, max: 100 },
  flow_m3_h: { min: 0.01, max: 10000 },
} as const;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function number(value: number | undefined, digits = 2) {
  return value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

export default function Simulations() {
  const { summary, headers, isLoading } = useWorkspaceData();
  const equipment = (summary as { equipment?: { id: string; tag: string } } | null)?.equipment;
  const [scenario, setScenario] = useState({ temperature_c: 185, pressure_bar: 10, flow_m3_h: 72 });
  const [durationS, setDurationS] = useState(3600);
  const [modelId, setModelId] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!headers) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/api/v1/models`, { headers, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) return;
        const items = ((await response.json()) as { items?: ModelOption[] }).items ?? [];
        setModels(items.filter((item) => ["VALIDATED", "PRODUCTION"].includes(item.status)));
      })
      .catch(() => { /* model list is optional for simulations */ });
    return () => controller.abort();
  }, [headers]);

  const update = useCallback((key: keyof typeof SCENARIO_LIMITS, raw: string) => {
    // Keep the text field responsive while typing; invalid text becomes NaN and
    // is rejected at submit with a clear message instead of being coerced to 0.
    setScenario((previous) => ({ ...previous, [key]: raw === "" ? Number.NaN : Number(raw) }));
  }, []);

  const runSimulation = useCallback(async () => {
    if (!equipment || !headers) { setError("No equipment is available for simulation."); return; }
    const values = [scenario.temperature_c, scenario.pressure_bar, scenario.flow_m3_h];
    if (!values.every(Number.isFinite)) { setError("Enter finite numeric operating conditions."); return; }
    const [temperature, pressure, flow] = values;
    const outside = (Object.keys(SCENARIO_LIMITS) as Array<keyof typeof SCENARIO_LIMITS>)
      .filter((key) => {
        const current = { temperature_c: temperature, pressure_bar: pressure, flow_m3_h: flow }[key];
        const limits = SCENARIO_LIMITS[key];
        return current < limits.min || current > limits.max;
      });
    if (outside.length) {
      setError(`The CSTR model accepts: temperature 100–300 °C, pressure 0.1–100 bar, flow 0.01–10000 m³/h.`);
      return;
    }
    if (!Number.isFinite(durationS) || durationS <= 0 || durationS > 86400) { setError("Duration must be between 1 and 86400 seconds."); return; }
    setBusy(true);
    setError("");
    setMessage("Running the CSTR physics model…");
    try {
      const response = await fetch(`${apiUrl}/api/v1/simulations`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          equipment_id: equipment.id,
          temperature_c: temperature,
          pressure_bar: pressure,
          flow_m3_h: flow,
          duration_s: durationS,
          ...(modelId ? { model_version_id: modelId } : {}),
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        setError(data.error?.message ?? data.detail?.message ?? `Simulation failed (HTTP ${response.status}).`);
        setMessage("");
        return;
      }
      setSimulation(data as Simulation);
      setMessage("Simulation completed. This response is advisory only; no plant setting was changed.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Network error during simulation.");
      setMessage("");
    } finally {
      setBusy(false);
    }
  }, [durationS, equipment, headers, modelId, scenario]);

  if (isLoading) return <div className="console-loading">Loading simulation inputs…</div>;
  if (!equipment) {
    return (
      <section className="panel empty-state">
        <h2>No equipment configured</h2>
        <p>Configure equipment before running a CSTR scenario.</p>
      </section>
    );
  }

  return (
    <div className="simulations-view">
      <p className="notice">
        What-if scenarios call the tenant's CSTR model and persist the request/result for auditability. They do not connect to or command a plant.
      </p>
      <p className="message" role="status">{message}</p>
      {error && <div className="error-state" role="alert"><strong>Simulation rejected.</strong> {error}</div>}
      <section className="grid">
        <article className="panel scenario">
          <p className="eyebrow">CSTR SCENARIO · {equipment.tag}</p>
          <h2>Operating conditions</h2>
          <div className="controls">
            <label>
              Temperature °C
              <input
                type="number" min={SCENARIO_LIMITS.temperature_c.min} max={SCENARIO_LIMITS.temperature_c.max} step="any"
                value={Number.isFinite(scenario.temperature_c) ? scenario.temperature_c : ""}
                onChange={(event) => update("temperature_c", event.target.value)}
                disabled={busy}
              />
            </label>
            <label>
              Pressure bar
              <input
                type="number" min={SCENARIO_LIMITS.pressure_bar.min} max={SCENARIO_LIMITS.pressure_bar.max} step="any"
                value={Number.isFinite(scenario.pressure_bar) ? scenario.pressure_bar : ""}
                onChange={(event) => update("pressure_bar", event.target.value)}
                disabled={busy}
              />
            </label>
            <label>
              Flow m³/h
              <input
                type="number" min={SCENARIO_LIMITS.flow_m3_h.min} max={SCENARIO_LIMITS.flow_m3_h.max} step="any"
                value={Number.isFinite(scenario.flow_m3_h) ? scenario.flow_m3_h : ""}
                onChange={(event) => update("flow_m3_h", event.target.value)}
                disabled={busy}
              />
            </label>
            <label>
              Duration s
              <input
                type="number" min={1} max={86400} step="any"
                value={durationS}
                onChange={(event) => setDurationS(Number(event.target.value))}
                disabled={busy}
              />
            </label>
            <label>
              Model
              <select value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={busy}>
                <option value="">Reference model (unvalidated)</option>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>{model.name} v{model.version} · {model.status}</option>
                ))}
              </select>
            </label>
          </div>
          <button onClick={() => void runSimulation()} disabled={busy}>
            {busy ? "Simulating…" : "Run simulation"}
          </button>
        </article>
        <article className="panel">
          <p className="eyebrow">MODEL RESPONSE</p>
          <h2>{simulation ? "Scenario metrics" : "Awaiting a run"}</h2>
          {simulation ? (
            <>
              <div className="metrics compact-metrics">
                <Metric label="Yield" value={number(simulation.scenario.yield_pct)} unit="%" />
                <Metric label="Conversion" value={number(simulation.scenario.conversion_pct)} unit="%" />
                <Metric label="Energy proxy" value={number(simulation.scenario.energy_proxy_kw)} unit="kW" />
              </div>
              <p className="muted">
                Δ yield {simulation.difference.yield_percentage_points >= 0 ? "+" : ""}{number(simulation.difference.yield_percentage_points)} pp ·
                Δ energy {number(simulation.difference.energy_kw)} kW
              </p>
              <span className={`pill ${simulation.model_validity === "VALIDATED_MODEL_RANGE" ? "good" : ""}`}>{simulation.model_validity}</span>
              {simulation.constraint_violations.map((item) => <p className="warning" key={item}>{item}</p>)}
            </>
          ) : (
            <div className="empty-state">Change an input and run the model to receive returned metrics and a trajectory.</div>
          )}
        </article>
      </section>
      {simulation && (
        <section className="panel">
          <div className="panel-title">
            <div>
              <p className="eyebrow">RETURNED TRAJECTORY</p>
              <h2>Model response over time</h2>
            </div>
            <span className="pill">{simulation.trajectory.length} samples</span>
          </div>
          <div className="plot">
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={simulation.trajectory}>
                <XAxis dataKey="time_s" tick={{ fill: "#86a0ae", fontSize: 11 }} tickFormatter={(value) => `${Number(value).toFixed(0)}s`} />
                <YAxis yAxisId="left" tick={{ fill: "#86a0ae", fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fill: "#86a0ae", fontSize: 11 }} />
                <Tooltip labelFormatter={(label) => `t = ${label}s`} />
                <Line yAxisId="left" type="monotone" dataKey="temperature_c" stroke="#60d5c6" dot={false} name="Temperature °C" />
                <Line yAxisId="right" type="monotone" dataKey="yield_pct" stroke="#f2b36d" dot={false} name="Yield %" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          {simulation.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
          <p className="muted">{simulation.advisory}</p>
        </section>
      )}
    </div>
  );
}

function Metric({ label, value, unit }: { label: string; value: string; unit: string }) {
  return <article className="metric"><p>{label}</p><h2>{value}<small>{unit}</small></h2></article>;
}
