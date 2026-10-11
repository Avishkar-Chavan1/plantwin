"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, messageOf, readErrorMessage } from "./api-client";
import { useWorkspaceData } from "./workspace-data";

export type ResourceState<T> = {
  data: T | null;
  error: string | null;
  /** True only while there is nothing to show yet (first load or path change). */
  loading: boolean;
  /** True during any in-flight request, including background refreshes. */
  fetching: boolean;
  reload: () => Promise<void>;
};

/**
 * GET a JSON resource through the authenticated client with loading/error/empty-friendly state,
 * request cancellation on unmount or path change, and optional visibility-aware polling.
 * Pass `null` to disable the request (for example while a prerequisite id is unknown).
 */
export function useApiResource<T>(path: string | null, options: { pollMs?: number } = {}): ResourceState<T> {
  const { apiFetch, headers } = useWorkspaceData();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [fetching, setFetching] = useState(false);
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;
  const controllerRef = useRef<AbortController | null>(null);
  const pathRef = useRef(path);
  const { pollMs } = options;
  const authenticated = Boolean(headers);

  const load = useCallback(async () => {
    const target = pathRef.current;
    if (!target || !authenticated) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setFetching(true);
    try {
      const response = await apiFetchRef.current(target, { signal: controller.signal });
      if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response, "Request failed."));
      const body = (await response.json()) as T;
      if (controller.signal.aborted) return;
      setData(body);
      setError(null);
    } catch (caught) {
      if (controller.signal.aborted) return;
      setError(messageOf(caught, "Request failed."));
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        setFetching(false);
      }
    }
  }, [authenticated]);

  useEffect(() => {
    pathRef.current = path;
    setData(null);
    setError(null);
    setLoading(Boolean(path));
    if (!path) return undefined;
    void load();
    let timer: number | undefined;
    if (pollMs) {
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") void load();
      }, pollMs);
    }
    return () => {
      controllerRef.current?.abort();
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [path, pollMs, load]);

  return { data, error, loading, fetching, reload: load };
}
