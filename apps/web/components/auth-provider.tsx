"use client";

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from "react";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface AuthState {
  token: string | null;
  organization: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const STORAGE_KEY = "processtwin-auth";

function loadAuthFromStorage(): { token: string | null; organization: string | null } {
  if (typeof window === "undefined") return { token: null, organization: null };
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return { token: parsed.token ?? null, organization: parsed.organization ?? null };
    }
  } catch {
    // ignore parse errors
  }
  return { token: null, organization: null };
}

function saveAuthToStorage(token: string | null, organization: string | null) {
  if (typeof window === "undefined") return;
  if (token && organization) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, organization }));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [organization, setOrganization] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const { token: storedToken, organization: storedOrg } = loadAuthFromStorage();
    setToken(storedToken);
    setOrganization(storedOrg);
    setIsLoading(false);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const response = await fetch(`${apiUrl}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        return { success: false, error: "Sign-in failed. Confirm that demo data has been seeded." };
      }

      const data = await response.json();
      const orgId = data.organizations?.[0]?.id ?? null;

      setToken(data.access_token);
      setOrganization(orgId);
      saveAuthToStorage(data.access_token, orgId);

      return { success: true };
    } catch {
      return { success: false, error: "Network error. Please try again." };
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setOrganization(null);
    saveAuthToStorage(null, null);
  }, []);

  return (
    <AuthContext.Provider value={{ token, organization, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}