"use client";

import { useEffect, useState, use } from "react";
import { useAuth } from "../../components/auth-provider";
import { ProcessTwinConsole } from "../../components/process-twin-console";
import { AuthGuard } from "../../components/auth-guard";

function ProductViewContent({ params }: { params: Promise<{ view: string[] }> }) {
  const { token, organization, isLoading } = useAuth();
  const [summary, setSummary] = useState<any>(null);
  const [plants, setPlants] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [isLoadingData, setIsLoadingData] = useState(true);

  const resolvedParams = use(params);
  const initialView = resolvedParams.view[0] ?? "dashboard";

  const headers = { Authorization: `Bearer ${token!}`, "X-Organization-ID": organization!, "Content-Type": "application/json" };

  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  useEffect(() => {
    const loadData = async () => {
      setIsLoadingData(true);
      try {
        const [summaryRes, plantsRes] = await Promise.all([
          fetch(`${apiUrl}/api/v1/dashboard/summary`, { headers }),
          fetch(`${apiUrl}/api/v1/plants`, { headers }),
        ]);
        if (summaryRes.ok) setSummary(await summaryRes.json());
        if (plantsRes.ok) {
          const data = await plantsRes.json();
          setPlants(data.items ?? []);
        }
      } catch {
        // ignore
      } finally {
        setIsLoadingData(false);
      }
    };
    loadData();
  }, [headers]);

  if (isLoading || isLoadingData) {
    return <div className="console-loading">Loading...</div>;
  }

  return (
    <ProcessTwinConsole
      initialView={initialView}
      token={token!}
      organization={organization!}
      headers={headers}
      summary={summary}
      plants={plants}
      setMessage={setMessage}
      isLoading={false}
    />
  );
}

export default async function ProductView({ params }: { params: Promise<{ view: string[] }> }) {
  return (
    <AuthGuard>
      <ProductViewContent params={params} />
    </AuthGuard>
  );
}