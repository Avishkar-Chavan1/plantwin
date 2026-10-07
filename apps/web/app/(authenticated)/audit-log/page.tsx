"use client";

import { useCallback, useEffect, useState } from "react";
import { useWorkspaceData } from "@/components/workspace-data";

type AuditEntry = {
  id: string;
  action: string;
  resource: string;
  user_id: string | null;
  timestamp: string;
  metadata: Record<string, unknown> | null;
};

export default function AuditLog() {
  const { apiFetch, isLoading: workspaceLoading } = useWorkspaceData();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/v1/audit-log?limit=200");
      if (!response.ok) throw new Error(`Unable to load the audit log (HTTP ${response.status}).`);
      const body = (await response.json()) as { items?: AuditEntry[] };
      setEntries(body.items ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load the audit log.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => { void load(); }, [load]);

  if (workspaceLoading || (loading && entries.length === 0 && !error)) {
    return <div className="console-loading" role="status">Loading audit log…</div>;
  }

  return (
    <div className="audit-log-view">
      <section className="panel">
        <div className="panel-title">
          <div>
            <p className="eyebrow">TENANT ACTIVITY RECORD</p>
            <h2>Audit log</h2>
          </div>
          <button type="button" className="secondary" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <p className="muted">Security-relevant actions are recorded with tenant scope. Entries are append-only.</p>
        {error && <div className="error-state" role="alert"><strong>Audit log unavailable.</strong> {error}</div>}
        {entries.length === 0 && !error ? (
          <div className="empty-state">
            <h2>No audit entries yet</h2>
            <p>Sign-ins, simulations, imports and model lifecycle transitions are recorded here as they happen.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Actor</th>
                  <th>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.id}>
                    <td>{new Date(entry.timestamp).toLocaleString()}</td>
                    <td><strong>{entry.action}</strong></td>
                    <td>{entry.resource}</td>
                    <td>{entry.user_id ? `${entry.user_id.slice(0, 8)}…` : "system"}</td>
                    <td>
                      {entry.metadata && Object.keys(entry.metadata).length > 0 ? (
                        <details>
                          <summary className="muted">View</summary>
                          <pre className="data-json">{JSON.stringify(entry.metadata, null, 2)}</pre>
                        </details>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
