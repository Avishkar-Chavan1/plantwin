"use client";

import { useDashboardData } from "@/components/use-dashboard-data";
import { HistoricalDataExplorer } from "@/components/historical-data-explorer";

export default function Data() {
  const { token, organization, isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading data...</div>;

  return (
    <div className="data-view">
      <HistoricalDataExplorer token={token!} organization={organization!} />
    </div>
  );
}
