"use client";

import { FormEvent, useEffect, useState } from "react";
import { Badge, formatNumber } from "@/components/status";
import { messageOf, readErrorMessage } from "@/components/api-client";
import { useApiResource } from "@/components/use-api-resource";
import { useWorkspaceData } from "@/components/workspace-data";

type Model = { id: string; name: string; version: string; type: string; status: string };
type Result = {
  headline: string;
  baseline: Record<string, number>;
  optimized: Record<string, number> & { variables: { temperature_c: number; pressure_bar: number; flow_m3_h: number } };
  objective_improvement: number;
  energy_impact_kw: number;
  constraints: { status: string };
  advisory: string;
  model_validity: string;
  recommendation_id: string | null;
  recommendation_gate: { status: "RECOMMENDATION_ALLOWED" | "NO_RECOMMENDATION"; basis: string; reasons: string[]; cautions: string[] };
};

const OPTIMIZATION_TIMEOUT_MS = 120_000;

export default function Optimization() {
  const { summary, apiFetch, isLoading } = useWorkspaceData();
  const equipment = (summary as { equipment?: { id: string; tag: string } | null } | null)?.equipment;
  const registry = useApiResource<{ items: Array<Model & { operating_envelope: unknown }> }>("/api/v1/models");
  const models = (registry.data?.items ?? []).filter((item) => ["VALIDATED", "STAGING", "PRODUCTION"].includes(item.status) && item.type === "physics_cstr");
  const [modelId, setModelId] = useState("");
  useEffect(() => { setModelId((current) => current || models[0]?.id || ""); }, [models]);
  const [inputs, setInputs] = useState({ temperature_c: 180, pressure_bar: 10, flow_m3_h: 72, energy_weight: 0.02 });
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(event: FormEvent) {
    event.preventDefault();
    if (!equipment || !modelId) { setError("A VALIDATED, STAGING or PRODUCTION CSTR model is required before optimization."); return; }
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/v1/optimization/runs", { method: "POST", json: { equipment_id: equipment.id, model_version_id: modelId, ...inputs }, timeoutMs: OPTIMIZATION_TIMEOUT_MS });
      if (!response.ok) throw new Error(await readErrorMessage(response, "The optimization was rejected."));
      setResult((await response.json()) as Result);
    } catch (caught) {
      setError(messageOf(caught, "Optimization failed."));
    } finally {
      setBusy(false);
    }
  }
  const numeric = (key: keyof typeof inputs) => (event: React.ChangeEvent<HTMLInputElement>) => setInputs({ ...inputs, [key]: Number(event.target.value) });

  if (isLoading || registry.loading) return <div className="console-loading" role="status">Loading optimization inputs…</div>;
  if (!equipment) return <section className="panel empty-state"><h2>No equipment configured</h2><p>Configure equipment before requesting an optimization run.</p></section>;
  const gate = result?.recommendation_gate;
  return (
    <div className="optimization-view">
      <p className="notice">Bounded optimization runs only against a validated CSTR model and its operating envelope. The output is an advisory scenario for engineer review; it is never sent to the plant.</p>
      {(error || registry.error) && <div className="error-state" role="alert"><strong>Optimization unavailable.</strong> {error || registry.error}</div>}
      <section className="grid">
        <form className="panel" onSubmit={(event) => void run(event)}>
          <p className="eyebrow">BOUNDED OPTIMIZATION · {equipment.tag}</p>
          <h2>Baseline operating point</h2>
          {models.length ? (
            <label>Model<select value={modelId} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.name} v{model.version} · {model.status}</option>)}</select></label>
          ) : <div className="empty-state">No VALIDATED, STAGING or PRODUCTION CSTR model exists. Register and independently validate one under Models first.</div>}
          <div className="controls">
            <label>Temperature °C<input type="number" step="any" min={170} max={190} value={inputs.temperature_c} onChange={numeric("temperature_c")} /></label>
            <label>Pressure bar<input type="number" step="any" min={8} max={12} value={inputs.pressure_bar} onChange={numeric("pressure_bar")} /></label>
            <label>Flow m³/h<input type="number" step="any" min={57.6} max={86.4} value={inputs.flow_m3_h} onChange={numeric("flow_m3_h")} /></label>
            <label>Energy weight<input type="number" step="0.01" min={0} max={1} value={inputs.energy_weight} onChange={numeric("energy_weight")} /></label>
          </div>
          <button type="submit" disabled={busy || !models.length}>{busy ? "Optimizing…" : "Run bounded optimization"}</button>
        </form>
        <article className="panel" aria-live="polite">
          <p className="eyebrow">ADVISORY OUTPUT</p>
          {!result ? <><h2>Awaiting a run</h2><div className="empty-state">Results appear here with their data basis, constraints and any reasons a recommendation was withheld.</div></> : gate?.status === "NO_RECOMMENDATION" ? (
            <>
              <h2>NO RECOMMENDATION</h2>
              <p className="warning"><strong>The system withheld advice because:</strong></p>
              <ul className="plain-list">{gate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
              <p className="muted">Basis: {gate.basis.replaceAll("_", " ").toLowerCase()}. Resolve the cause (for example restore a stale connector or move to an in-range operating point) and run again.</p>
            </>
          ) : (
            <>
              <h2>Advisory scenario</h2>
              <Badge tone="warn">REQUIRES ENGINEER REVIEW</Badge> <Badge tone="idle">{result.model_validity.replaceAll("_", " ")}</Badge>
              <div className="metrics compact-metrics">
                <article className="metric"><p>Yield</p><h2>{formatNumber(result.optimized.yield_pct)}<small>%</small></h2></article>
                <article className="metric"><p>Conversion</p><h2>{formatNumber(result.optimized.conversion_pct)}<small>%</small></h2></article>
                <article className="metric"><p>Energy impact</p><h2>{formatNumber(result.energy_impact_kw)}<small>kW</small></h2></article>
              </div>
              <p>Scenario point for review: <strong>{formatNumber(result.optimized.variables.temperature_c, 1)} °C · {formatNumber(result.optimized.variables.pressure_bar, 1)} bar · {formatNumber(result.optimized.variables.flow_m3_h, 1)} m³/h</strong></p>
              {gate?.cautions.map((caution) => <p className="warning" key={caution}>{caution}</p>)}
              <p className="muted">{result.advisory} A recommendation was queued under Recommendations for engineer review.</p>
            </>
          )}
        </article>
      </section>
    </div>
  );
}