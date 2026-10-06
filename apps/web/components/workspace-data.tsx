"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useAuth } from "./auth-provider";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ApiHeaders = { Authorization: string; "X-Organization-ID": string; "Content-Type": string };
export type PlantSummary = { id: string; name: string; location: string | null };

type WorkspaceData = {
  summary: Record<string, unknown> | null;
  plants: PlantSummary[];
  headers: ApiHeaders | null;
  token: string | null;
  organization: string | null;
  isLoading: boolean;
  error: string | null;
  message: string;
  setMessage: (message: string) => void;
  refresh: () => Promise<void>;
};

const WorkspaceDataContext = createContext<WorkspaceData | null>(null);

async function responseMessage(response: Response, fallback: string) {
  try {
    const body = await response.json() as { detail?: { message?: string }; error?: { message?: string } };
    return body.detail?.message ?? body.error?.message ?? fallback;
  } catch {
    return fallback;
  }
}

export function WorkspaceDataProvider({ children }: { children: React.ReactNode }) {
  const { token, organization } = useAuth();
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [plants, setPlants] = useState<PlantSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const headers = useMemo<ApiHeaders | null>(() => token && organization ? {
    Authorization: `Bearer ${token}`,
    "X-Organization-ID": organization,
    "Content-Type": "application/json",
  } : null, [token, organization]);

  const refresh = useCallback(async () => {
    if (!headers) {
      setIsLoading(false);
      return;
    }
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    try {
      const [summaryResponse, plantsResponse] = await Promise.all([
        fetch(`${apiUrl}/api/v1/dashboard/summary`, { headers, signal: controller.signal }),
        fetch(`${apiUrl}/api/v1/plants?limit=100`, { headers, signal: controller.signal }),
      ]);
      if (!summaryResponse.ok) throw new Error(await responseMessage(summaryResponse, "Unable to load the process summary."));
      if (!plantsResponse.ok) throw new Error(await responseMessage(plantsResponse, "Unable to load plants."));
      const [summaryBody, plantsBody] = await Promise.all([
        summaryResponse.json() as Promise<Record<string, unknown>>,
        plantsResponse.json() as Promise<{ items?: PlantSummary[] }>,
      ]);
      setSummary(summaryBody);
      setPlants(plantsBody.items ?? []);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setError(caught instanceof Error ? caught.message : "Unable to load workspace data.");
      }
    } finally {
      setIsLoading(false);
    }
  }, [headers]);

  useEffect(() => { void refresh(); }, [refresh]);

  const value = useMemo(() => ({
    summary, plants, headers, token, organization, isLoading, error, message, setMessage, refresh,
  }), [summary, plants, headers, token, organization, isLoading, error, message, refresh]);

  return <WorkspaceDataContext.Provider value={value}>{children}</WorkspaceDataContext.Provider>;
}

export function useWorkspaceData() {
  const context = useContext(WorkspaceDataContext);
  if (!context) throw new Error("useWorkspaceData must be used within WorkspaceDataProvider");
  return context;
}
