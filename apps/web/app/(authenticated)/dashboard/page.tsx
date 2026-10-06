"use client";

import { useDashboardData } from "@/components/use-dashboard-data";
import { ProcessTwinConsole } from "@/components/process-twin-console";

export default function Dashboard() {
  const { summary, isLoading, token, organization, headers, setMessage } = useDashboardData();

  if (isLoading) return <div className="console-loading">Loading...</div>;

  return (
    <ProcessTwinConsole
      initialView="dashboard"
      token={token!}
      organization={organization!}
      headers={headers ?? {}}
      summary={summary as any}
      setMessage={setMessage}
      isLoading={false}
    />
  );
}
