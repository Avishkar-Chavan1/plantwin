"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./auth-provider";
import { API_URL, ApiError, describeFailure, readErrorMessage } from "./api-client";

export { ApiError } from "./api-client";
export type ApiHeaders = { Authorization: string; "X-Organization-ID": string; "Content-Type": string };
export type PlantSummary = { id: string; name: string; location: string | null };
export type ApiInit = RequestInit & { json?: unknown; timeoutMs?: number };

/** Summary refresh cadence while the tab is visible; paused when hidden, backed off on errors. */
const SUMMARY_POLL_MS = 15_000;
const SUMMARY_ERROR_POLL_MS = 45_000;
const DEFAULT_TIMEOUT_MS = 30_000;

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
   * Authenticated fetch with a timeout, one automatic retry after a token refresh, and network
   * failures surfaced as `ApiError` (status 0) rather than raw TypeErrors. JSON bodies are
   * serialized; FormData is passed through so the browser sets the multipart boundary.
   */
  apiFetch: (path: string, init?: ApiInit) => Promise<Response>;
};

const WorkspaceDataContext = createContext<WorkspaceData | null>(null);

export function WorkspaceDataProvider({ children }: { children: React.ReactNode }) {
  const { token, organization, refreshSession } = useAuth();
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [plants, setPlants] = useState<PlantSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const inFlight = useRef<AbortController | null>(null);
  const loadedOnce = useRef(false);
  const failing = useRef(false);

  const headers = useMemo<ApiHeaders | null>(() => token && organization ? {
    Authorization: `Bearer ${token}`,
    "X-Organization-ID": organization,
    "Content-Type": "application/json",
  } : null, [token, organization]);

  const apiFetch = useCallback(async (path: string, init: ApiInit = {}) => {
    if (!headers) throw new ApiError(401, "Your session has ended. Sign in again.");
    const { json, timeoutMs = DEFAULT_TIMEOUT_MS, signal: callerSignal, ...rest } = init;
    const send = async (authorization: string) => {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
      const abortFromCaller = () => controller.abort(callerSignal?.reason);
      callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
      const requestHeaders: Record<string, string> = {
        ...(rest.headers as Record<string, string> | undefined),
        Authorization: authorization,
        "X-Organization-ID": headers["X-Organization-ID"],
      };
      let body = rest.body;
      if (json !== undefined) {
        requestHeaders["Content-Type"] = "application/json";
        body = JSON.stringify(json);
      }
      // A body implies a write to ProcessTwin's own API; never let it silently become a GET.
      const method = rest.method ?? (body !== undefined && body !== null ? "POST" : "GET");
      try {
        return await fetch(`${API_URL}${path}`, { ...rest, method, headers: requestHeaders, body, signal: controller.signal });
      } catch (caught) {
        if (callerSignal?.aborted) throw caught; // caller cancelled: not a failure to report
        throw new ApiError(0, describeFailure(caught, timeoutMs));
      } finally {
        window.clearTimeout(timer);
        callerSignal?.removeEventListener("abort", abortFromCaller);
      }
    };

    let response = await send(headers.Authorization);
    if (response.status === 401) {
      // Access tokens expire; rotate once and retry before surfacing an error.
      const renewed = await refreshSession();
      if (renewed) response = await send(`Bearer ${renewed}`);
    }
    return response;
  }, [headers, refreshSession]);

  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  const refresh = useCallback(async () => {
    if (!headers) {
      setIsLoading(false);
      return;
    }
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    // Only the first load shows a spinner; background refreshes keep the current data on screen.
    if (!loadedOnce.current) setIsLoading(true);
    try {
      const [summaryResponse, plantsResponse] = await Promise.all([
        apiFetchRef.current("/api/v1/dashboard/summary", { signal: controller.signal }),
        apiFetchRef.current("/api/v1/plants?limit=100", { signal: controller.signal }),
      ]);
      if (!summaryResponse.ok) throw new ApiError(summaryResponse.status, await readErrorMessage(summaryResponse, "Unable to load the process summary."));
      if (!plantsResponse.ok) throw new ApiError(plantsResponse.status, await readErrorMessage(plantsResponse, "Unable to load plants."));
      const [summaryBody, plantsBody] = await Promise.all([
        summaryResponse.json() as Promise<Record<string, unknown>>,
        plantsResponse.json() as Promise<{ items?: PlantSummary[] }>,
      ]);
      if (controller.signal.aborted) return;
      setSummary(summaryBody);
      setPlants(plantsBody.items ?? []);
      setError(null);
      failing.current = false;
      loadedOnce.current = true;
    } catch (caught) {
      if (controller.signal.aborted) return;
      failing.current = true;
      setError(caught instanceof Error ? caught.message : "Unable to load workspace data.");
    } finally {
      if (!controller.signal.aborted) setIsLoading(false);
    }
  }, [headers]);

  // Initial load plus visibility-aware polling. A fresh timer chain starts whenever auth changes.
  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    const schedule = (delay: number) => {
      timer = window.setTimeout(async () => {
        if (stopped) return;
        if (document.visibilityState === "visible") await refresh();
        if (!stopped) schedule(failing.current ? SUMMARY_ERROR_POLL_MS : SUMMARY_POLL_MS);
      }, delay);
    };
    void refresh().then(() => { if (!stopped) schedule(SUMMARY_POLL_MS); });
    const onVisible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      inFlight.current?.abort();
    };
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
