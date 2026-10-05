"use client";

import { useDashboardData } from "../../components/use-dashboard-data";
import { ProcessTwinConsole } from "../../components/process-twin-console";

export default function AuditLog() {
  const { summary, plants, isLoading, message, setMessage, token, organization } = useDashboardData();
  const headers = token && organization
    ? { Authorization: `Bearer ${token}`, "X-Organization-ID": organization, "Content-Type": "application/json" }
    : undefined;

  if (isLoading || !token || !organization) {
    return <div className="console-loading">Loading...</div>;
  }

  return (
    <ProcessTwinConsole
      initialView="audit-log"
      token={token}
      organization={organization}
      headers={headers!}
      summary={summary}
      plants={plants}
      setMessage={setMessage}
      isLoading={false}
    />
  );
}