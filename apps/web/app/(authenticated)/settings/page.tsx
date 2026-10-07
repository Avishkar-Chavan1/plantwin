"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { useWorkspaceData } from "@/components/workspace-data";

type Me = {
  id: string;
  email: string;
  organizations: Array<{ id: string; role: string }>;
};

function tokenExpiry(token: string | null): string {
  if (!token) return "—";
  try {
    const payload = JSON.parse(atob(token.split(".")[1] ?? "")) as { exp?: number };
    return payload.exp ? new Date(payload.exp * 1000).toLocaleString() : "—";
  } catch {
    return "—";
  }
}

export default function Settings() {
  const { apiFetch, organization } = useWorkspaceData();
  const { token, logout } = useAuth();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/v1/auth/me");
      if (!response.ok) throw new Error(`Unable to load account details (HTTP ${response.status}).`);
      setMe((await response.json()) as Me);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load account details.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => { void load(); }, [load]);

  const membership = me?.organizations.find((entry) => entry.id === organization);

  return (
    <div className="settings-view">
      <section className="panel">
        <div className="panel-title">
          <div>
            <p className="eyebrow">ACCOUNT</p>
            <h2>Session and account</h2>
          </div>
          <button type="button" className="secondary" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        {error && <div className="error-state" role="alert"><strong>Account details unavailable.</strong> {error}</div>}
        {loading && !me && !error ? (
          <div className="console-loading" role="status">Loading account…</div>
        ) : me ? (
          <div className="table-wrap">
            <table>
              <tbody>
                <tr><th>Account</th><td>{me.email}</td></tr>
                <tr><th>Organization</th><td>{organization ?? "—"}</td></tr>
                <tr><th>Role</th><td>{membership?.role ?? "—"}</td></tr>
                <tr><th>Access token expires</th><td>{tokenExpiry(token)}</td></tr>
                <tr><th>Session policy</th><td>Access tokens rotate automatically while the browser session stays open; signing out revokes the refresh server-side.</td></tr>
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
      <section className="panel">
        <p className="eyebrow">OPERATING BOUNDARY</p>
        <h2>Read-only advisory system</h2>
        <p className="muted">
          ProcessTwin is a monitoring, simulation and advisory product. It has no write path to PLCs, DCS or
          SCADA equipment: connectors are read-only by design and every simulation or recommendation is
          labelled with its model validity. Any implementation of a recommendation happens in the plant's own
          control system after human review.
        </p>
        <div className="actions">
          <button type="button" onClick={() => void logout()}>Sign out of this session</button>
        </div>
      </section>
    </div>
  );
}
