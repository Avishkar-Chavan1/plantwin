"use client";

import { FormEvent, useEffect, useState } from "react";

type ModelVersion = {
  id: string;
  name: string;
  version: number;
  type: string;
  status: "CALIBRATED" | "EVALUATED" | "VALIDATION" | "VALIDATED" | "STAGING" | "PRODUCTION" | "RETIRED";
  metrics: Record<string, unknown>;
  operating_envelope: Record<string, unknown> | null;
  dataset_version_id: string | null;
  git_sha: string | null;
  created_at: string;
};

type Evaluation = {
  id: string;
  evaluation_type: string;
  metrics: Record<string, Record<string, number>>;
  observation_count: number;
  status: string;
  created_at: string;
};

type DriftEvent = {
  id: string;
  status: string;
  metrics: Record<string, unknown>;
  reasons: string[] | null;
  created_at: string;
};

type Calibration = {
  id: string;
  method: string;
  objective: string;
  status: string;
  observation_count: number;
  metrics: Record<string, unknown>;
  model_version_id: string | null;
  created_at: string;
};

type RoleInfo = { role: string };

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const navigation = ["dashboard", "plants", "equipment", "sensors", "digital-twins", "simulations", "optimization", "recommendations", "alerts", "models", "data", "data-sources", "settings", "audit-log"];

function numericEntries(metrics: Record<string, unknown>): Array<[string, number]> {
  const flat: Array<[string, number]> = [];
  for (const [signal, value] of Object.entries(metrics)) {
    if (typeof value === "number") flat.push([signal, value]);
    else if (value && typeof value === "object") {
      for (const [metric, inner] of Object.entries(value as Record<string, unknown>)) {
        if (typeof inner === "number") flat.push([`${signal}.${metric}`, inner]);
      }
    }
  }
  return flat;
}

export function ModelGovernance({ token, organization }: { token: string; organization: string }) {
  const headers = { Authorization: `Bearer ${token}`, "X-Organization-ID": organization, "Content-Type": "application/json" };
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [calibrations, setCalibrations] = useState<Calibration[]>([]);
  const [role, setRole] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string>("");
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [driftEvents, setDriftEvents] = useState<DriftEvent[]>([]);
  const [message, setMessage] = useState("Select a model version to review its evidence before any lifecycle transition.");
  const [busy, setBusy] = useState(false);

  async function reload() {
    const [modelsResponse, calibrationsResponse, meResponse] = await Promise.all([
      fetch(`${apiUrl}/api/v1/models`, { headers }),
      fetch(`${apiUrl}/api/v1/calibrations`, { headers }),
      fetch(`${apiUrl}/api/v1/auth/me`, { headers }),
    ]);
    if (modelsResponse.ok) {
      const items: ModelVersion[] = (await modelsResponse.json()).items;
      setModels(items);
      if (!selectedId && items.length) setSelectedId(items[0].id);
    }
    if (calibrationsResponse.ok) setCalibrations((await calibrationsResponse.json()).items);
    if (meResponse.ok) {
      const me: { organizations: Array<{ id: string; role: string }> } = await meResponse.json();
      setRole(me.organizations.find((entry) => entry.id === organization)?.role ?? "");
    }
  }

  async function loadEvidence(modelId: string) {
    if (!modelId) { setEvaluations([]); setDriftEvents([]); return; }
    const [evaluationsResponse, driftResponse] = await Promise.all([
      fetch(`${apiUrl}/api/v1/models/${modelId}/evaluations`, { headers }),
      fetch(`${apiUrl}/api/v1/models/${modelId}/drift-events`, { headers }),
    ]);
    setEvaluations(evaluationsResponse.ok ? (await evaluationsResponse.json()).items : []);
    setDriftEvents(driftResponse.ok ? (await driftResponse.json()).items : []);
  }

  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [token, organization]);
  useEffect(() => { void loadEvidence(selectedId); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [selectedId]);

  const selected = models.find((entry) => entry.id === selectedId);
  const canGovern = role === "OWNER" || role === "ADMIN";
  const actionableEvaluations = evaluations.filter((item) => item.status === "EVALUATED" && item.observation_count >= 10);

  async function post(path: string, body?: Record<string, unknown>): Promise<string> {
    const response = await fetch(`${apiUrl}${path}`, { method: "POST", headers, body: body ? JSON.stringify(body) : undefined });
    const data = await response.json();
    if (!response.ok) return data.detail?.message ?? data.error?.message ?? `Request failed (${response.status})`;
    return "";
  }

  async function submitValidation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    const evaluationId = String(form.get("evaluation_id") ?? "");
    const evaluation = evaluations.find((item) => item.id === evaluationId);
    if (!evaluation) { setMessage("Choose the independent evaluation that backs this validation."); return; }
    const acceptanceLimits: Record<string, Record<string, { minimum?: number; maximum?: number }>> = {};
    for (const [signal, metrics] of Object.entries(evaluation.metrics)) {
      for (const metric of Object.keys(metrics)) {
        const minimum = form.get(`min::${signal}::${metric}`);
        const maximum = form.get(`max::${signal}::${metric}`);
        const bounds: { minimum?: number; maximum?: number } = {};
        if (minimum !== null && minimum !== "") bounds.minimum = Number(minimum);
        if (maximum !== null && maximum !== "") bounds.maximum = Number(maximum);
        if (bounds.minimum !== undefined || bounds.maximum !== undefined) {
          acceptanceLimits[signal] ??= {};
          acceptanceLimits[signal][metric] = bounds;
        }
      }
    }
    setBusy(true);
    const error = await post(`/api/v1/models/${selected.id}/validate`, {
      evaluation_id: evaluationId,
      acceptance_limits: acceptanceLimits,
      review_note: String(form.get("review_note") ?? ""),
    });
    setBusy(false);
    setMessage(error || `Model ${selected.name} v${selected.version} validated with recorded acceptance limits. Stage it next to approach production.`);
    await reload();
    await loadEvidence(selected.id);
  }

  async function transition(action: "stage" | "promote") {
    if (!selected) return;
    const prompt = action === "stage"
      ? `Stage ${selected.name} v${selected.version} for production review?`
      : `Promote ${selected.name} v${selected.version} to PRODUCTION? The current production version of this model will be retired.`;
    if (!window.confirm(prompt)) return;
    setBusy(true);
    const error = await post(`/api/v1/models/${selected.id}/${action}`);
    setBusy(false);
    setMessage(error || `Model ${selected.name} v${selected.version} ${action === "stage" ? "staged" : "promoted"}; the transition is recorded in the audit log.`);
    await reload();
  }

  return <main className="shell">
    <aside><div className="logo"><span>◈</span> ProcessTwin</div><p className="tenant">MODEL GOVERNANCE</p><nav>{navigation.map((entry) => <a className={entry === "models" ? "active" : ""} href={`/${entry}`} key={entry}>{entry.replaceAll("-", " ")}</a>)}</nav><div className="operator"><span className="dot" /> Role: {role || "…"}<small>No automatic promotion</small></div></aside>
    <section className="workspace">
      <header><div><p className="eyebrow">REGISTRY / EVALUATION / DRIFT</p><h1>Model governance</h1></div><span className="pill">HUMAN VALIDATION REQUIRED</span></header>
      <p className="notice">Lifecycle: CALIBRATED → EVALUATED → VALIDATION → VALIDATED → STAGING → PRODUCTION → RETIRED. Validation needs an independent evaluation with explicit acceptance limits; staging and promotion are OWNER/ADMIN actions recorded in the audit log. Drift events never retrain or replace a production model.</p>
      <p className="message">{message}</p>

      <section className="panel"><div className="panel-title"><div><p className="eyebrow">MODEL VERSIONS</p><h2>Registry</h2></div><button type="button" className="secondary" onClick={() => void reload()}>Refresh</button></div>
        {models.length ? <div style={{ overflowX: "auto" }}><table style={{ width: "100%", textAlign: "left" }}><thead><tr>{["Model", "Version", "Type", "Status", "Metrics", "Envelope", "Git", "Created", ""].map((text) => <th key={text} style={{ padding: 9 }}>{text}</th>)}</tr></thead><tbody>{models.map((entry) => <tr key={entry.id} style={entry.id === selectedId ? { outline: "1px solid #60d5c6" } : undefined}>
          <td>{entry.name}</td><td>v{entry.version}</td><td>{entry.type}</td>
          <td><span className={`pill ${entry.status === "PRODUCTION" ? "good" : ""}`}>{entry.status}</span></td>
          <td>{numericEntries(entry.metrics).slice(0, 3).map(([key, value]) => <div key={key}><small>{key} {value.toFixed(4)}</small></div>)}</td>
          <td>{entry.operating_envelope && Object.keys(entry.operating_envelope).length ? `${Object.keys(entry.operating_envelope).length} signals` : "—"}</td>
          <td><small>{entry.git_sha ?? "—"}</small></td><td>{new Date(entry.created_at).toLocaleDateString()}</td>
          <td><button type="button" className="secondary" onClick={() => setSelectedId(entry.id)}>Review</button></td>
        </tr>)}</tbody></table></div> : <p>No registered model versions. Train a model first; training only registers VALIDATION candidates.</p>}
      </section>

      {selected && <section className="panel" style={{ marginTop: 18 }}>
        <div className="panel-title"><div><p className="eyebrow">SELECTED MODEL</p><h2>{selected.name} v{selected.version} · {selected.status}</h2></div>
          <span className="pill">{canGovern ? "GOVERNANCE ACTIONS ENABLED" : "READ-ONLY FOR YOUR ROLE"}</span></div>
        <div className="impact"><span>Dataset <b>{selected.dataset_version_id ? "linked" : "—"}</b></span><span>Envelope <b>{selected.operating_envelope && Object.keys(selected.operating_envelope).length ? "configured" : "missing"}</b></span><span>Observations <b>{evaluations.reduce((sum, item) => sum + item.observation_count, 0)}</b></span></div>

        <h3 style={{ margin: "16px 0 6px" }}>Independent evaluations</h3>
        {evaluations.length ? <div style={{ overflowX: "auto" }}><table style={{ width: "100%", textAlign: "left" }}><thead><tr>{["Type", "Status", "Observations", "Key metrics", "Created"].map((text) => <th key={text} style={{ padding: 9 }}>{text}</th>)}</tr></thead><tbody>{evaluations.map((item) => <tr key={item.id}>
          <td>{item.evaluation_type}</td><td><span className={`pill ${item.status === "EVALUATED" ? "good" : ""}`}>{item.status}</span></td><td>{item.observation_count}</td>
          <td>{numericEntries(item.metrics).slice(0, 4).map(([key, value]) => <div key={key}><small>{key} {value.toFixed(4)}</small></div>)}</td>
          <td>{new Date(item.created_at).toLocaleString()}</td>
        </tr>)}</tbody></table></div> : <p>No evaluations yet — run an evaluation against an independent dataset version first.</p>}

        <h3 style={{ margin: "16px 0 6px" }}>Drift events</h3>
        {driftEvents.length ? <div style={{ overflowX: "auto" }}><table style={{ width: "100%", textAlign: "left" }}><thead><tr>{["Status", "Metrics", "Reasons", "Created"].map((text) => <th key={text} style={{ padding: 9 }}>{text}</th>)}</tr></thead><tbody>{driftEvents.map((item) => <tr key={item.id}>
          <td><span className={`pill ${item.status === "OK" ? "good" : ""}`}>{item.status}</span></td>
          <td>{numericEntries(item.metrics).slice(0, 4).map(([key, value]) => <div key={key}><small>{key} {value.toFixed(4)}</small></div>)}</td>
          <td>{(item.reasons ?? []).join(", ") || "—"}</td><td>{new Date(item.created_at).toLocaleString()}</td>
        </tr>)}</tbody></table></div> : <p>No drift events recorded for this model.</p>}

        {(selected.status === "VALIDATION" || selected.status === "CALIBRATED" || selected.status === "EVALUATED" || selected.status === "VALIDATED") && <form className="panel" onSubmit={submitValidation} style={{ display: "grid", gap: 14, marginTop: 18 }}>
          <p className="eyebrow">VALIDATION REVIEW · OWNER / ADMIN</p>
          <h3>Record explicit acceptance limits</h3>
          {actionableEvaluations.length ? <>
            <label>Evaluation<select name="evaluation_id" required><option value="" disabled>Choose an independent evaluation</option>{actionableEvaluations.map((item) => <option key={item.id} value={item.id}>{item.evaluation_type} · {item.observation_count} obs · {new Date(item.created_at).toLocaleString()}</option>)}</select></label>
            <p>Limits are prefilled from the observed metrics (you must review and adjust them). Every bound you keep is checked server-side before the status can change.</p>
            {actionableEvaluations.map((item) => <fieldset key={item.id} style={{ border: "1px solid #25445a", padding: 12 }}>
              <legend><small>{item.evaluation_type} acceptance bounds</small></legend>
              {Object.entries(item.metrics).map(([signal, metrics]) => Object.entries(metrics).map(([metric, value]) => <div className="controls" key={`${signal}::${metric}`}>
                <label style={{ flex: 2 }}><small>{signal} · {metric} = {value.toFixed(4)}</small></label>
                <label>Minimum<input name={`min::${signal}::${metric}`} type="number" step="any" defaultValue={value} /></label>
                <label>Maximum<input name={`max::${signal}::${metric}`} type="number" step="any" defaultValue={value} /></label>
              </div>))}
            </fieldset>)}
            <label>Review note (min 30 characters)<textarea name="review_note" required minLength={30} maxLength={4000} rows={3} placeholder="Reviewed residuals by regime and holdout period; limits signed by the reviewing engineer." /></label>
            <button type="submit" disabled={busy || !canGovern}>{busy ? "Validating…" : "Validate with these limits"}</button>
          </> : <p>No evaluation qualifies yet: validation requires a successful independent evaluation with at least 10 measured observations.</p>}
        </form>}

        {selected.status === "VALIDATED" && canGovern && <div className="actions" style={{ marginTop: 14 }}><button type="button" disabled={busy} onClick={() => void transition("stage")}>Stage for production review</button></div>}
        {selected.status === "STAGING" && canGovern && <div className="actions" style={{ marginTop: 14 }}><button type="button" disabled={busy} onClick={() => void transition("promote")}>Promote to production</button></div>}
        {!canGovern && <p><small>Your role ({role || "unknown"}) cannot change lifecycle state. Owner or admin sign-off is required.</small></p>}
      </section>}

      <section className="panel" style={{ marginTop: 18 }}><p className="eyebrow">CALIBRATION HISTORY</p><h2>Recent calibration runs</h2>
        {calibrations.length ? <div style={{ overflowX: "auto" }}><table style={{ width: "100%", textAlign: "left" }}><thead><tr>{["Method", "Objective", "Status", "Observations", "Metrics", "Created"].map((text) => <th key={text} style={{ padding: 9 }}>{text}</th>)}</tr></thead><tbody>{calibrations.slice(0, 10).map((item) => <tr key={item.id}>
          <td>{item.method}</td><td>{item.objective}</td><td><span className={`pill ${item.status === "COMPLETED" ? "good" : ""}`}>{item.status}</span></td><td>{item.observation_count}</td>
          <td>{numericEntries(item.metrics).slice(0, 3).map(([key, value]) => <div key={key}><small>{key} {value.toFixed(4)}</small></div>)}</td>
          <td>{new Date(item.created_at).toLocaleString()}</td>
        </tr>)}</tbody></table></div> : <p>No calibration runs recorded for this tenant.</p>}
      </section>
    </section>
  </main>;
}
