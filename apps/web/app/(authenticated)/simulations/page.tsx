"use client";

import { useCallback, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, formatNumber } from "@/components/status";
import { messageOf, readErrorMessage } from "@/components/api-client";
import { useApiResource } from "@/components/use-api-resource";
import { useWorkspaceData } from "@/components/workspace-data";

const SIMULATION_TIMEOUT_MS = 90_000;

type Simulation = {
  id: string;
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

export default function Simulations() {
  const { summary, apiFetch, isLoading } = useWorkspaceData();
  const equipment = (summary as { equipment?: { id: string; tag: string } } | null)?.equipment;
  const [scenario, setScenario] = useState({ temperature_c: 185, pressure_bar: 10, flow_m3_h: 72 });
  const [durationS, setDurationS] = useState(3600);
  const [modelId, setModelId] = useState("");
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [busy, setBusy] = useState(false);
  const [completedAt, setCompletedAt] = useState<Date | null>(null);
  const [error, setError] = useState("");
  // The model list is optional: without it the unvalidated reference model is still usable.
  const registry = useApiResource<{ items: ModelOption[] }>("/api/v1/models");
  const models = (registry.data?.items ?? []).filter((item) => ["VALIDATED", "PRODUCTION"].includes(item.status));

  const update = useCallback((key: keyof typeof SCENARIO_LIMITS, raw: string) => {
    // Keep the text field responsive while typing; invalid text becomes NaN and
    // is rejected at submit with a clear message instead of being coerced to 0.
    setScenario((previous) => ({ ...previous, [key]: raw === "" ? Number.NaN : Number(raw) }));
  }, []);

  const runSimulation = useCallback(async () => {
    if (!equipment) { setError("No equipment is available for simulation."); return; }
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
    try {
      const response = await apiFetch("/api/v1/simulations", {
        method: "POST",
        timeoutMs: SIMULATION_TIMEOUT_MS,
        json: {
          equipment_id: equipment.id,
          temperature_c: temperature,
          pressure_bar: pressure,
          flow_m3_h: flow,
          duration_s: durationS,
          ...(modelId ? { model_version_id: modelId } : {}),
        },
      });
      if (!response.ok) throw new Error(await readErrorMessage(response, "The simulation was rejected."));
      // Replacing the whole result (new id) is what redraws the chart; nothing is animated client-side.
      setSimulation((await response.json()) as Simulation);
      setCompletedAt(new Date());
    } catch (caught) {
      setError(messageOf(caught, "Network error during simulation."));
    } finally {
      setBusy(false);
    }
  }, [apiFetch, durationS, equipment, modelId, scenario]);

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
        What-if scenarios call the organization&apos;s CSTR model and persist the request/result for auditability. They do not connect to or command a plant.
      </p>
      <p className="message" role="status" aria-live="polite">{busy ? "Running the CSTR physics model…" : simulation && completedAt ? `Simulation ${simulation.id.slice(0, 8)} completed at ${completedAt.toLocaleTimeString()}. Advisory only; no plant setting was changed.` : ""}</p>
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
          <p className="eyebrow">MODEL RESPONSE · <Badge tone="sim">SIMULATION</Badge></p>
          <h2>{simulation ? "Scenario metrics" : "Awaiting a run"}</h2>
          {simulation ? (
            <>
              <div className="metrics compact-metrics">
                <Metric label="Yield" value={formatNumber(simulation.scenario.yield_pct)} unit="%" />
                <Metric label="Conversion" value={formatNumber(simulation.scenario.conversion_pct)} unit="%" />
                <Metric label="Energy proxy" value={formatNumber(simulation.scenario.energy_proxy_kw)} unit="kW" />
              </div>
              <p className="muted">
                Δ yield {simulation.difference.yield_percentage_points >= 0 ? "+" : ""}{formatNumber(simulation.difference.yield_percentage_points)} pp ·
                Δ energy {formatNumber(simulation.difference.energy_kw)} kW
              </p>
              <Badge tone={simulation.model_validity === "VALIDATED_MODEL_RANGE" ? "live" : "warn"}>{simulation.model_validity.replaceAll("_", " ")}</Badge>
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
              <LineChart key={simulation.id} data={simulation.trajectory}>
                <XAxis dataKey="time_s" tick={{ fill: "#86a0ae", fontSize: 11 }} tickFormatter={(value) => `${Number(value).toFixed(0)}s`} />
                <YAxis yAxisId="left" tick={{ fill: "#86a0ae", fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fill: "#86a0ae", fontSize: 11 }} />
                <Tooltip labelFormatter={(label) => `t = ${label}s`} />
                <Line yAxisId="left" type="monotone" dataKey="temperature_c" stroke="#60d5c6" dot={false} name="Temperature °C (simulated)" isAnimationActive={false} />
                <Line yAxisId="right" type="monotone" dataKey="yield_pct" stroke="#f2b36d" dot={false} name="Yield % (simulated)" isAnimationActive={false} />
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
