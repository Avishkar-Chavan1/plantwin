"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./auth-provider";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ApiHeaders = { Authorization: string; "X-Organization-ID": string; "Content-Type": string };
export type PlantSummary = { id: string; name: string; location: string | null };

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

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
  /**
   * Authenticated fetch with one automatic retry after a token refresh.
   * JSON bodies are serialized and given the JSON content type; FormData is
   * passed through untouched so the browser can set the multipart boundary.
   */
  apiFetch: (path: string, init?: RequestInit & { json?: unknown }) => Promise<Response>;
};

const WorkspaceDataContext = createContext<WorkspaceData | null>(null);

async function responseMessage(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { detail?: { message?: string } | string; error?: { message?: string } };
    if (typeof body.detail === "string") return body.detail;
    return body.detail?.message ?? body.error?.message ?? fallback;
  } catch {
    return fallback;
  }
}

export function WorkspaceDataProvider({ children }: { children: React.ReactNode }) {
  const { token, organization, refreshSession } = useAuth();
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [plants, setPlants] = useState<PlantSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const inFlight = useRef<AbortController | null>(null);

  const headers = useMemo<ApiHeaders | null>(() => token && organization ? {
    Authorization: `Bearer ${token}`,
    "X-Organization-ID": organization,
    "Content-Type": "application/json",
  } : null, [token, organization]);

  const apiFetch = useCallback(async (path: string, init: RequestInit & { json?: unknown } = {}) => {
    if (!headers) throw new ApiError(401, "Your session has ended. Sign in again.");
    const { json, ...rest } = init;
    const buildInit = (authorization: string): RequestInit => {
      const requestHeaders: Record<string, string> = {
        Authorization: authorization,
        "X-Organization-ID": headers["X-Organization-ID"],
      };
      if (json !== undefined) {
        requestHeaders["Content-Type"] = "application/json";
        return { ...rest, headers: requestHeaders, body: JSON.stringify(json) };
      }
      if (rest.body instanceof FormData || rest.body instanceof Blob || typeof rest.body === "string") {
        // Leave multipart/binary bodies to the caller; only add auth headers.
        return { ...rest, headers: { ...rest.headers, Authorization: authorization, "X-Organization-ID": headers["X-Organization-ID"] } };
      }
      requestHeaders["Content-Type"] = "application/json";
      return { ...rest, headers: { ...rest.headers, ...requestHeaders } };
    };

    let response = await fetch(`${apiUrl}${path}`, buildInit(headers.Authorization));
    if (response.status === 401) {
      // Access tokens expire; rotate once and retry before surfacing an error.
      const renewed = await refreshSession();
      if (renewed) {
        response = await fetch(`${apiUrl}${path}`, buildInit(renewed));
      }
    }
    return response;
  }, [headers, refreshSession]);

  const refresh = useCallback(async () => {
    if (!headers) {
      setIsLoading(false);
      return;
    }
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
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
      if (controller.signal.aborted) return;
      setSummary(summaryBody);
      setPlants(plantsBody.items ?? []);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setError(caught instanceof Error ? caught.message : "Unable to load workspace data.");
      }
    } finally {
      if (!controller.signal.aborted) setIsLoading(false);
    }
  }, [headers]);

  useEffect(() => {
    void refresh();
    return () => inFlight.current?.abort();
  }, [refresh]);

  const value = useMemo(() => ({
    summary, plants, headers, token, organization, isLoading, error, message, setMessage, refresh, apiFetch,
  }), [summary, plants, headers, token, organization, isLoading, error, message, refresh, apiFetch]);

  return <WorkspaceDataContext.Provider value={value}>{children}</WorkspaceDataContext.Provider>;
}

export function useWorkspaceData() {
  const context = useContext(WorkspaceDataContext);
  if (!context) throw new Error("useWorkspaceData must be used within WorkspaceDataProvider");
  return context;
}
