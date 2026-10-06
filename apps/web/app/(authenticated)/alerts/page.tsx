"use client";

import { useDashboardData } from "@/components/use-dashboard-data";

export default function Alerts() {
  const { isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading alerts...</div>;

  return (
    <div className="alerts-view">
      <section className="grid">
        <article className="panel">
          <p className="eyebrow">SAFETY & NOTIFICATIONS</p>
          <h2>Alerts</h2>
          <p>Active alerts and safety notices.</p>
          <div className="alert-item">
            <span className="severity">INFO</span>
            <p>System operating normally</p>
          </div>
        </article>
      </section>
    </div>
  );
}
