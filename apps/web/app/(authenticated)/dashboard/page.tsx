"use client";

import { ProcessTwinConsole } from "@/components/process-twin-console";
import { useDashboardData } from "@/components/use-dashboard-data";

export default function Dashboard() {
  const { summary, token, organization, headers, setMessage, isLoading } = useDashboardData();

  // While the workspace summary is still in flight, show a loading state instead of
  // letting the console fall through to "No equipment configured", which is a
  // misleading thing to tell an operator who does have equipment configured.
  if (!token || !organization || !headers || (isLoading && !summary)) {
    return <div className="console-loading" role="status">Loading process twin…</div>;
  }

  return (
    <ProcessTwinConsole
      token={token}
      organization={organization}
      headers={headers}
      summary={summary as Record<string, never> | null}
      setMessage={setMessage}
    />
  );
}
