"use client";

import { useEffect, useState, useCallback, ReactNode, useMemo } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "./auth-provider";

const navigation = [
  { key: "dashboard", label: "Dashboard", href: "/dashboard" },
  { key: "plants", label: "Plants", href: "/plants" },
  { key: "equipment", label: "Equipment", href: "/equipment" },
  { key: "sensors", label: "Sensors", href: "/sensors" },
  { key: "digital-twins", label: "Digital Twins", href: "/digital-twins" },
  { key: "simulations", label: "Simulations", href: "/simulations" },
  { key: "optimization", label: "Optimization", href: "/optimization" },
  { key: "recommendations", label: "Recommendations", href: "/recommendations" },
  { key: "alerts", label: "Alerts", href: "/alerts" },
  { key: "models", label: "Models", href: "/models" },
  { key: "data", label: "Data", href: "/data" },
  { key: "data-sources", label: "Data Sources", href: "/data-sources" },
  { key: "settings", label: "Settings", href: "/settings" },
  { key: "audit-log", label: "Audit Log", href: "/audit-log" },
];

interface AuthenticatedLayoutProps {
  children: ReactNode;
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const { token, organization, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [summary, setSummary] = useState<any>(null);
  const [plants, setPlants] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  // Memoize headers so it doesn't change every render
  const headers = useMemo(() =>
    token && organization
      ? { Authorization: `Bearer ${token}`, "X-Organization-ID": organization, "Content-Type": "application/json" }
      : undefined,
    [token, organization]
  );

  const loadSummary = useCallback(async () => {
    if (!headers) return;
    try {
      const response = await fetch(`${apiUrl}/api/v1/dashboard/summary`, { headers });
      if (response.ok) {
        setSummary(await response.json());
      }
    } catch {
      // ignore
    }
  }, [headers, apiUrl]);

  const loadPlants = useCallback(async () => {
    if (!headers) return;
    try {
      const response = await fetch(`${apiUrl}/api/v1/plants`, { headers });
      if (response.ok) {
        const data = await response.json();
        setPlants(data.items ?? []);
      }
    } catch {
      // ignore
    }
  }, [headers, apiUrl]);

  useEffect(() => {
    if (headers) {
      setIsLoading(true);
      loadSummary();
      loadPlants();
      setIsLoading(false);
    }
  }, [headers, loadSummary, loadPlants]);

  const handleLogout = useCallback(() => {
    logout();
    router.push("/login");
    router.refresh();
  }, [logout, router]);

  const getCurrentView = () => {
    if (pathname === "/dashboard") return "dashboard";
    if (pathname.startsWith("/")) {
      const parts = pathname.split("/").filter(Boolean);
      return parts[0] ?? "dashboard";
    }
    return "dashboard";
  };

  const currentView = getCurrentView();

  // Determine if we're in an implemented view that should show the full shell
  const implementedViews = ["dashboard", "simulations", "models", "data", "data-sources"];
  const isImplementedView = implementedViews.includes(currentView);

  if (isLoading || !token || !organization) {
    return <div className="loading-shell" style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>Loading...</div>;
  }

  const m = summary?.measurements ?? {};

  return (
    <div className="shell">
      <aside>
        <div className="logo"><span>◈</span> ProcessTwin</div>
        <p className="tenant">SIMULATION ENVIRONMENT</p>
        <nav>
          {navigation.map((entry) => (
            <Link
              key={entry.key}
              href={entry.href}
              className={pathname === entry.href ? "active" : ""}
            >
              {entry.label}
            </Link>
          ))}
        </nav>
        <div className="operator">
          <span className="dot" />
          Human-in-the-loop
          <br />
          <small>No control connection</small>
        </div>
        <button className="logout-btn" onClick={handleLogout}>
          Logout
        </button>
      </aside>
      <section className="workspace">
        <header>
          <div>
            <p className="eyebrow">
              PROCESS TWIN / {summary?.equipment?.tag ?? "R-101"}
            </p>
            <h1>{currentView.replaceAll("-", " ")}</h1>
          </div>
          <div className="header-status">
            <span className={`pill ${summary?.plant_health === "GOOD" ? "good" : ""}`}>
              {summary?.plant_health ?? "LOADING"}
            </span>
            <span>
              {summary?.source_mode === "LIVE_READ_ONLY"
                ? "LIVE READ-ONLY MODE"
                : summary?.source_mode === "HISTORICAL"
                ? "HISTORICAL MODE"
                : summary?.source_mode === "MIXED_DATA_BLOCKED"
                ? "MIXED DATA BLOCKED"
                : "SIMULATION MODE"}
            </span>
          </div>
        </header>
        {summary?.safety_notice && <p className="notice">{summary.safety_notice}</p>}
        {message && <p className="message">{message}</p>}
        <div className="content-area">
          {children}
        </div>
      </section>
    </div>
  );
}
