"use client";

import { useState } from "react";
import { Badge, formatTimestamp } from "@/components/status";
import { messageOf, readErrorMessage } from "@/components/api-client";
import { useApiResource } from "@/components/use-api-resource";
import { useWorkspaceData } from "@/components/workspace-data";

type Decision = "REVIEWED" | "ACCEPTED" | "REJECTED" | "EXPIRED";
type Recommendation = {
  id: string;
  text: string;
  reason: string;
  status: "GENERATED" | "REVIEWED" | "ACCEPTED" | "REJECTED" | "EXPIRED";
  expected_benefit: { yield_percentage_points?: number } | null;
  energy_impact: { change_kw?: number } | null;
  proposed_change: { temperature_c?: number; pressure_bar?: number; flow_m3_h?: number } | null;
  baseline: { basis?: string; cautions?: string[] } | null;
  operating_constraints: { bounds?: Record<string, unknown>; constraints?: Record<string, unknown> };
  constraint_status: string | null;
  confidence: string;
  uncertainty_quantified: boolean;
  validation: { model_status: string; real_plant_validation: string };
  source: { kind: string; basis: string; algorithm: string | null; model: { name: string; version: string } | null };
  allowed_decisions: Decision[];
  expires_at: string | null;
  timestamp: string;
  advisory: string;
};

const DECISION_LABEL: Record<Decision, string> = { REVIEWED: "Mark reviewed", ACCEPTED: "Accept for procedure review", REJECTED: "Reject", EXPIRED: "Expire / withdraw" };
const BASIS_LABEL: Record<string, string> = {
  LIVE_READ_ONLY_STATE: "Live read-only plant state",
  SIMULATION_STATE: "Simulated state",
  HISTORICAL_STATE: "Historical state",
  USER_SUPPLIED_SCENARIO: "Engineer-supplied scenario (no plant data)",
};

function fixed(value: number | undefined | null, digits = 1) {
  return value === undefined || value === null ? "—" : value.toFixed(digits);
}

export default function Recommendations() {
  const { apiFetch, isLoading } = useWorkspaceData();
  const list = useApiResource<{ items: Recommendation[] }>("/api/v1/recommendations?limit=100");
  const [openFor, setOpenFor] = useState("");
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function review(id: string, decision: Decision, comment: string) {
    setBusyId(id);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(`/api/v1/recommendations/${id}/review`, { method: "POST", json: { decision, comment } });
      if (!response.ok) throw new Error(await readErrorMessage(response, "The review could not be recorded."));
      setMessage(`Decision ${decision} recorded and written to the audit log. No plant setting was changed.`);
      setOpenFor("");
      await list.reload();
    } catch (caught) {
      setError(messageOf(caught, "The review could not be recorded."));
    } finally {
      setBusyId("");
    }
  }

  if (isLoading || list.loading) return <div className="console-loading" role="status">Loading recommendations…</div>;
  const items = list.data?.items ?? [];
  return (
    <div className="recommendations-view">
      <p className="notice"><strong>Advisory recommendations — each requires engineer review.</strong> They are model scenario results to be checked against operating procedures. ProcessTwin sends no commands to PLC, DCS, SCADA or plant equipment.</p>
      {message && <p className="message" role="status">{message}</p>}
      {(error || list.error) && <div className="error-state" role="alert"><strong>Request failed.</strong> {error || list.error}<button className="secondary" onClick={() => void list.reload()}>Retry</button></div>}
      <section className="panel">
        <div className="panel-title">
          <div><p className="eyebrow">ADVISORY QUEUE</p><h2>Recommendations</h2></div>
          <button type="button" className="secondary" onClick={() => void list.reload()} disabled={list.fetching}>{list.fetching ? "Refreshing…" : "Refresh"}</button>
        </div>
        {items.length === 0 && !list.error ? (
          <div className="empty-state">
            <h2>No recommendations</h2>
            <p>A recommendation is produced only when a validated model, fresh good-quality data and an in-range operating point all support it. Otherwise an optimization run reports NO RECOMMENDATION with the reasons. See Optimization.</p>
          </div>
        ) : (
          <div className="rec-list">
            {items.map((item) => (
              <article className="rec-card" key={item.id}>
                <header className="rec-head">
                  <div>
                    <Badge tone="warn">ADVISORY · REQUIRES ENGINEER REVIEW</Badge> <Badge tone={item.status === "ACCEPTED" ? "live" : item.status === "REJECTED" || item.status === "EXPIRED" ? "idle" : "sim"}>{item.status}</Badge>
                    <h3>{item.text}</h3>
                  </div>
                  <small>{formatTimestamp(item.timestamp)}{item.expires_at ? ` · expires ${formatTimestamp(item.expires_at)}` : ""}</small>
                </header>
                <p className="rec-reason"><strong>Reason.</strong> {item.reason}</p>
                <dl className="rec-facts">
                  <div><dt>Proposed operating point (for review)</dt><dd>{item.proposed_change ? `${fixed(item.proposed_change.temperature_c)} °C · ${fixed(item.proposed_change.pressure_bar)} bar · ${fixed(item.proposed_change.flow_m3_h)} m³/h` : "—"}</dd></div>
                  <div><dt>Expected effect</dt><dd>{item.expected_benefit?.yield_percentage_points !== undefined ? `${item.expected_benefit.yield_percentage_points >= 0 ? "+" : ""}${fixed(item.expected_benefit.yield_percentage_points, 2)} pp yield` : "Not quantified"}{item.energy_impact?.change_kw !== undefined ? ` · ${fixed(item.energy_impact.change_kw)} kW energy` : ""}</dd></div>
                  <div><dt>Confidence</dt><dd>{item.confidence}</dd></div>
                  <div><dt>Uncertainty</dt><dd>{item.uncertainty_quantified ? "Quantified (see model record)" : "Not quantified for this operating point"}</dd></div>
                  <div><dt>Operating constraints</dt><dd>Constraint status {item.constraint_status ?? "UNKNOWN"}; bounds from the recorded model envelope{item.operating_constraints.bounds && Object.keys(item.operating_constraints.bounds).length ? "" : " (not recorded)"}</dd></div>
                  <div><dt>Data / model source</dt><dd>{BASIS_LABEL[item.source.basis] ?? item.source.basis} · {item.source.model ? `${item.source.model.name} v${item.source.model.version}` : "no registered model"}{item.source.algorithm ? ` · ${item.source.algorithm}` : ""}</dd></div>
                  <div><dt>Validation status</dt><dd>Model {item.validation.model_status} · real-plant validation: {item.validation.real_plant_validation.replaceAll("_", " ").toLowerCase()}</dd></div>
                </dl>
                {item.baseline?.cautions?.map((caution) => <p className="warning" key={caution}>{caution}</p>)}
                {item.allowed_decisions.length > 0 && (
                  openFor === item.id ? (
                    <form
                      className="review-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        const form = new FormData(event.currentTarget);
                        const comment = String(form.get("comment") ?? "").trim();
                        if (comment.length < 5) { setError("A review comment of at least 5 characters is required."); return; }
                        void review(item.id, form.get("decision") as Decision, comment);
                      }}
                    >
                      <label>Decision
                        <select name="decision" defaultValue={item.allowed_decisions[0]}>
                          {item.allowed_decisions.map((decision) => <option key={decision} value={decision}>{DECISION_LABEL[decision]}</option>)}
                        </select>
                      </label>
                      <label>Comment (recorded in the audit log)
                        <input name="comment" required minLength={5} maxLength={2000} autoFocus />
                      </label>
                      <button type="submit" disabled={busyId === item.id}>{busyId === item.id ? "Recording…" : "Record decision"}</button>
                      <button type="button" className="secondary" onClick={() => setOpenFor("")}>Cancel</button>
                    </form>
                  ) : <button type="button" className="secondary" onClick={() => { setOpenFor(item.id); setError(""); }}>Review…</button>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}