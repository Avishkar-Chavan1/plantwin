"use client";

import { useDashboardData } from "@/components/use-dashboard-data";

export default function Recommendations() {
  const { isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading recommendations...</div>;

  return (
    <div className="recommendations-view">
      <section className="grid">
        <article className="panel">
          <p className="eyebrow">ADVISOR</p>
          <h2>Recommendations</h2>
          <p>Physics-backed operating recommendations generated from optimization analysis.</p>
          <p>Recommendations never modify plant controls directly.</p>
        </article>
      </section>
    </div>
  );
}
