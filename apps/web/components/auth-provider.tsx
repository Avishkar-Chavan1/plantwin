"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const STORAGE_KEY = "processtwin-auth";
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

type StoredAuth = { token: string; refreshToken: string; organization: string };

interface AuthState {
  token: string | null;
  organization: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<boolean>;
}

const AuthContext = createContext<AuthState | null>(null);

function cookieValue(name: string): string | null {
  if (typeof document === "undefined") return null;
  const entry = document.cookie.split("; ").find((item) => item.startsWith(`${name}=`));
  return entry ? decodeURIComponent(entry.slice(name.length + 1)) : null;
}

function cookieAttributes(maxAgeSeconds: number): string {
  const secure = typeof window !== "undefined" && window.location.protocol === "https:" ? "; Secure" : "";
  return `path=/; max-age=${maxAgeSeconds}; SameSite=Lax${secure}`;
}

function writeCookie(name: string, value: string, maxAgeSeconds: number) {
  if (typeof document !== "undefined") document.cookie = `${name}=${encodeURIComponent(value)}; ${cookieAttributes(maxAgeSeconds)}`;
}

function clearCookie(name: string) {
  if (typeof document !== "undefined") document.cookie = `${name}=; ${cookieAttributes(0)}`;
}

function readStoredAuth(): StoredAuth | null {
  if (typeof window === "undefined") return null;
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<StoredAuth> | null;
    if (parsed?.token && parsed.refreshToken && parsed.organization) {
      return { token: parsed.token, refreshToken: parsed.refreshToken, organization: parsed.organization };
    }
  } catch {
    // A malformed client cache should not prevent a fresh sign-in.
  }
  const token = cookieValue("processtwin-token");
  const refreshToken = cookieValue("processtwin-refresh-token");
  const organization = cookieValue("processtwin-org");
  return token && refreshToken && organization ? { token, refreshToken, organization } : null;
}

function persistAuth(auth: StoredAuth | null) {
  if (typeof window === "undefined") return;
  if (!auth) {
    localStorage.removeItem(STORAGE_KEY);
    clearCookie("processtwin-token");
    clearCookie("processtwin-refresh-token");
    clearCookie("processtwin-org");
    return;
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
  // Middleware only uses these as a navigation gate. The API remains the authorization authority.
  writeCookie("processtwin-token", auth.token, SESSION_MAX_AGE_SECONDS);
  writeCookie("processtwin-refresh-token", auth.refreshToken, SESSION_MAX_AGE_SECONDS);
  writeCookie("processtwin-org", auth.organization, SESSION_MAX_AGE_SECONDS);
}

function errorMessage(body: unknown, fallback: string) {
  if (body && typeof body === "object") {
    const response = body as { detail?: { message?: string }; error?: { message?: string } };
    if (response.detail?.message) return response.detail.message;
    if (response.error?.message) return response.error.message;
  }
  return fallback;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Start in the same state on server and client. This prevents an authenticated
  // client render from hydrating over the server's unauthenticated shell.
  const [token, setToken] = useState<string | null>(null);
  const [organization, setOrganization] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const applyAuth = useCallback((auth: StoredAuth | null) => {
    setToken(auth?.token ?? null);
    setRefreshToken(auth?.refreshToken ?? null);
    setOrganization(auth?.organization ?? null);
    persistAuth(auth);
  }, []);

  const refreshSession = useCallback(async (): Promise<boolean> => {
    const stored = readStoredAuth();
    if (!stored?.refreshToken) return false;
    try {
      const response = await fetch(`${apiUrl}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: stored.refreshToken }),
      });
      const body = await response.json();
      if (!response.ok || !body.access_token || !body.refresh_token) return false;
      applyAuth({ token: body.access_token, refreshToken: body.refresh_token, organization: stored.organization });
      return true;
    } catch {
      return false;
    }
  }, [applyAuth]);

  useEffect(() => {
    let active = true;
    async function restore() {
      const stored = readStoredAuth();
      if (!stored) {
        if (active) setIsLoading(false);
        return;
      }
      try {
        const response = await fetch(`${apiUrl}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${stored.token}`, "X-Organization-ID": stored.organization },
        });
        if (response.ok) {
          if (active) applyAuth(stored);
        } else {
          const refreshed = await refreshSession();
          if (!refreshed && active) applyAuth(null);
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

  useEffect(() => {
    if (!refreshToken) return;
    const timer = window.setInterval(() => { void refreshSession(); }, 20 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [refreshSession, refreshToken]);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const response = await fetch(`${apiUrl}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await response.json();
      const selectedOrganization = body.organizations?.[0]?.id as string | undefined;
      if (!response.ok || !body.access_token || !body.refresh_token || !selectedOrganization) {
        return { success: false, error: errorMessage(body, "Sign-in failed. Check your credentials.") };
      }
      applyAuth({ token: body.access_token, refreshToken: body.refresh_token, organization: selectedOrganization });
      setIsLoading(false);
      return { success: true };
    } catch {
      return { success: false, error: "Network error. Please try again." };
    }
  }, [applyAuth]);

  const logout = useCallback(async () => {
    const stored = readStoredAuth();
    try {
      if (stored?.refreshToken) {
        await fetch(`${apiUrl}/api/v1/auth/logout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: stored.refreshToken }),
        });
      }
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
