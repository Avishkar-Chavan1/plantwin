"use client";

import { useDashboardData } from "@/components/use-dashboard-data";
import { ModelGovernance } from "@/components/model-governance";

export default function Models() {
  const { token, organization, isLoading } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading models...</div>;

  return (
    <div className="models-view">
      <ModelGovernance token={token!} organization={organization!} />
    </div>
  );
}
