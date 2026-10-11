"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  API_URL,
  AUTH_REQUEST_TIMEOUT_MS,
  describeFailure,
  LOGIN_REQUEST_TIMEOUT_MS,
  timeoutSignal,
} from "./api-client";

const STORAGE_KEY = "processtwin-auth";
/**
 * Non-secret marker read by middleware to decide whether to send an unauthenticated visitor to
 * /login. It carries no token. Tokens live only in localStorage and are sent as Authorization
 * headers by the browser; the API remains the sole authorization authority.
 */
const SESSION_MARKER = "processtwin-session";
const LEGACY_COOKIES = ["processtwin-token", "processtwin-refresh-token", "processtwin-org"];
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

type StoredAuth = { token: string; refreshToken: string; organization: string };

interface AuthState {
  token: string | null;
  organization: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  /** Rotate the refresh token; resolves with the new access token or null when it could not be renewed. */
  refreshSession: () => Promise<string | null>;
}

const AuthContext = createContext<AuthState | null>(null);

function cookieAttributes(maxAgeSeconds: number): string {
  const secure = typeof window !== "undefined" && window.location.protocol === "https:" ? "; Secure" : "";
  return `path=/; max-age=${maxAgeSeconds}; SameSite=Lax${secure}`;
}

function setMarker(active: boolean) {
  if (typeof document === "undefined") return;
  document.cookie = `${SESSION_MARKER}=${active ? "1" : ""}; ${cookieAttributes(active ? SESSION_MAX_AGE_SECONDS : 0)}`;
  // Tokens used to be mirrored into cookies; make sure no stale copy survives an upgrade.
  for (const name of LEGACY_COOKIES) document.cookie = `${name}=; ${cookieAttributes(0)}`;
}

function readStoredAuth(): StoredAuth | null {
  if (typeof window === "undefined") return null;
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<StoredAuth> | null;
    if (parsed?.token && parsed.refreshToken && parsed.organization) {
      return { token: parsed.token, refreshToken: parsed.refreshToken, organization: parsed.organization };
    }
  } catch {
    // A malformed client cache must not prevent a fresh sign-in.
  }
  return null;
}

function persistAuth(auth: StoredAuth | null) {
  if (typeof window === "undefined") return;
  if (!auth) localStorage.removeItem(STORAGE_KEY);
  else localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
  setMarker(auth !== null);
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { error?: { message?: string }; detail?: string | { message?: string } };
    if (body.error?.message) return body.error.message;
    if (typeof body.detail === "string") return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch {
    // fall through
  }
  if (response.status === 429) return "Too many sign-in attempts. Wait a minute and try again.";
  if (response.status >= 500) return "The ProcessTwin API is unavailable. Try again shortly.";
  return fallback;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Start in the same state on server and client so an authenticated client render never
  // hydrates over the server's unauthenticated shell.
  const [token, setToken] = useState<string | null>(null);
  const [organization, setOrganization] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshInFlight = useRef<Promise<string | null> | null>(null);

  const applyAuth = useCallback((auth: StoredAuth | null) => {
    setToken(auth?.token ?? null);
    setRefreshToken(auth?.refreshToken ?? null);
    setOrganization(auth?.organization ?? null);
    persistAuth(auth);
  }, []);

  const refreshSession = useCallback((): Promise<string | null> => {
    // Refresh tokens rotate and are single-use. Concurrent callers must share one request or the
    // loser presents an already-revoked token and the user is signed out.
    if (refreshInFlight.current) return refreshInFlight.current;
    const run = (async (): Promise<string | null> => {
      const stored = readStoredAuth();
      if (!stored?.refreshToken) return null;
      try {
        const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: stored.refreshToken }),
          signal: timeoutSignal(AUTH_REQUEST_TIMEOUT_MS),
        });
        if (response.status === 401 || response.status === 403) {
          applyAuth(null); // the refresh token is revoked or expired: the session is over
          return null;
        }
        const body = await response.json();
        if (!response.ok || !body.access_token || !body.refresh_token) return null;
        applyAuth({ token: body.access_token, refreshToken: body.refresh_token, organization: stored.organization });
        return body.access_token as string;
      } catch {
        return null; // transient network failure: keep the session, try again later
      }
    })().finally(() => { refreshInFlight.current = null; });
    refreshInFlight.current = run;
    return run;
  }, [applyAuth]);

  useEffect(() => {
    let active = true;
    async function restore() {
      const stored = readStoredAuth();
      if (!stored) {
        setMarker(false);
        if (active) setIsLoading(false);
        return;
      }
      try {
        const response = await fetch(`${API_URL}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${stored.token}`, "X-Organization-ID": stored.organization },
          signal: timeoutSignal(AUTH_REQUEST_TIMEOUT_MS),
        });
        if (response.ok) {
          if (active) applyAuth(stored);
        } else {
          const refreshed = await refreshSession();
          if (!refreshed && active && response.status === 401) applyAuth(null);
          else if (!refreshed && active) applyAuth(stored);
        }
      } catch {
        // Retain a previously valid session during a transient network outage.
        if (active) applyAuth(stored);
      } finally {
        if (active) setIsLoading(false);
      }
    }
    void restore();
    return () => { active = false; };
  }, [applyAuth, refreshSession]);

  // Keep tabs in step: signing out (or in) in one tab must be reflected in the others.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      const next = readStoredAuth();
      setToken(next?.token ?? null);
      setRefreshToken(next?.refreshToken ?? null);
      setOrganization(next?.organization ?? null);
      setMarker(next !== null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (!refreshToken) return;
    const timer = window.setInterval(() => { void refreshSession(); }, 20 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [refreshSession, refreshToken]);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const response = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        signal: timeoutSignal(LOGIN_REQUEST_TIMEOUT_MS),
      });
      if (!response.ok) return { success: false, error: await errorMessage(response, "Sign-in failed. Check your credentials.") };
      const body = await response.json();
      const selectedOrganization = body.organizations?.[0]?.id as string | undefined;
      if (!body.access_token || !body.refresh_token || !selectedOrganization) {
        return { success: false, error: "Your account has no organization access. Contact your administrator." };
      }
      applyAuth({ token: body.access_token, refreshToken: body.refresh_token, organization: selectedOrganization });
      setIsLoading(false);
      return { success: true };
    } catch (caught) {
      return { success: false, error: describeFailure(caught, LOGIN_REQUEST_TIMEOUT_MS) };
    }
  }, [applyAuth]);

  const logout = useCallback(async () => {
    const stored = readStoredAuth();
    try {
      if (stored?.refreshToken) {
        await fetch(`${API_URL}/api/v1/auth/logout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: stored.refreshToken }),
          signal: timeoutSignal(AUTH_REQUEST_TIMEOUT_MS),
        });
      }
    } catch {
      // Revocation is best effort; the local session is always cleared.
    } finally {
      applyAuth(null);
    }
  }, [applyAuth]);

  const value = useMemo(() => ({ token, organization, isLoading, login, logout, refreshSession }), [token, organization, isLoading, login, logout, refreshSession]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
