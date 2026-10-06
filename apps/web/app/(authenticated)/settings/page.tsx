"use client";

import { useDashboardData } from "@/components/use-dashboard-data";

export default function Settings() {
  const { isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading settings...</div>;

  return (
    <div className="settings-view">
      <section className="grid">
        <article className="panel">
          <p className="eyebrow">CONFIGURATION</p>
          <h2>Settings</h2>
          <p>System settings and preferences.</p>
          <ul>
            <li>User preferences</li>
            <li>Display options</li>
            <li>API configuration</li>
          </ul>
        </article>
      </section>
    </div>
  );
}
