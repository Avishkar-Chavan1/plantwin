"use client";

import { useDashboardData } from "@/components/use-dashboard-data";

export default function AuditLog() {
  const { isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading audit log...</div>;

  return (
    <div className="audit-log-view">
      <section className="grid">
        <article className="panel">
          <p className="eyebrow">SYSTEM ACTIVITY</p>
          <h2>Audit Log</h2>
          <p>Immutable record of system activity and API calls.</p>
          <p>All changes are tracked for compliance and debugging.</p>
        </article>
      </section>
    </div>
  );
}
