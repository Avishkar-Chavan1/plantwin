"use client";

import { useDashboardData } from "@/components/use-dashboard-data";
import { LiveDataSources } from "@/components/live-data-sources";

export default function DataSources() {
  const { token, organization, isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading data sources...</div>;

  return (
    <div className="data-sources-view">
      <LiveDataSources token={token!} organization={organization!} />
    </div>
  );
}
