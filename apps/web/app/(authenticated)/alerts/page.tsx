"use client";

import { useCallback, useEffect, useState } from "react";
import { useWorkspaceData } from "@/components/workspace-data";

type Alert = {
  id: string;
  equipment_id: string | null;
  severity: string;
  reason: string;
  status: string;
  timestamp: string;
};

function severityClass(severity: string) {
  if (severity === "CRITICAL") return "pill bad";
  if (severity === "WARNING") return "pill warn";
  return "pill";
}

export default function Alerts() {
  const { apiFetch, isLoading: workspaceLoading } = useWorkspaceData();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/v1/alerts?limit=100");
      if (!response.ok) throw new Error(`Unable to load alerts (HTTP ${response.status}).`);
      const body = (await response.json()) as { items?: Alert[] };
      setAlerts(body.items ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load alerts.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => { void load(); }, [load]);

  if (workspaceLoading || (loading && alerts.length === 0 && !error)) {
    return <div className="console-loading" role="status">Loading alerts…</div>;
  }

  return (
    <div className="alerts-view">
      <section className="panel">
        <div className="panel-title">
          <div>
            <p className="eyebrow">ACTIVE CONDITIONS</p>
            <h2>Alerts</h2>
          </div>
          <button type="button" className="secondary" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        {error && <div className="error-state" role="alert"><strong>Alerts unavailable.</strong> {error}</div>}
        {alerts.length === 0 && !error ? (
          <div className="empty-state">
            <h2>No alerts recorded</h2>
            <p>The twin synchronization service raises an alert when a potential anomaly is detected in the measured data. None are open for this organization.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Reason</th>
                  <th>Status</th>
                  <th>Detected</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((alert) => (
                  <tr key={alert.id}>
                    <td><span className={severityClass(alert.severity)}>{alert.severity}</span></td>
                    <td>{alert.reason}</td>
                    <td>{alert.status}</td>
                    <td>{new Date(alert.timestamp).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="notice compact">Alerts are advisory findings from data review. They are never sent to a plant control system.</p>
      </section>
    </div>
  );
}
