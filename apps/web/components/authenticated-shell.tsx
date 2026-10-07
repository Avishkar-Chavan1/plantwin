"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "./auth-provider";
import { useWorkspaceData, WorkspaceDataProvider } from "./workspace-data";

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

function pageTitle(pathname: string) {
  const key = pathname.split("/").filter(Boolean)[0] ?? "dashboard";
  return navigation.find((entry) => entry.key === key)?.label ?? "Workspace";
}

function ShellBody({ children }: { children: React.ReactNode }) {
  const { token, organization, logout } = useAuth();
  const { summary, isLoading, error, message, setMessage } = useWorkspaceData();
  const pathname = usePathname();
  const router = useRouter();
  const equipment = summary?.equipment as { tag?: string } | undefined;
  const sourceMode = String(summary?.source_mode ?? "NO_DATA");

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (!token || !organization) return <div className="loading-shell">Restoring secure session…</div>;

  return (
    <div className="shell">
      <aside aria-label="Primary navigation">
        <Link className="logo" href="/dashboard" aria-label="ProcessTwin dashboard"><span aria-hidden="true">◈</span> ProcessTwin</Link>
        <p className="tenant">READ-ONLY PROCESS INTELLIGENCE</p>
        <nav>
          {navigation.map((entry) => <Link key={entry.key} href={entry.href} className={pathname === entry.href ? "active" : ""}>{entry.label}</Link>)}
        </nav>
        <div className="operator"><span className="dot" /> Human-in-the-loop<br /><small>No plant control connection</small></div>
        <button className="logout-btn" onClick={() => void handleLogout()}>Sign out</button>
      </aside>
      <section className="workspace">
        <header>
          <div>
            <p className="eyebrow">PROCESS TWIN / {equipment?.tag ?? "TENANT WORKSPACE"}</p>
            <h1>{pageTitle(pathname)}</h1>
          </div>
          <div className="header-status">
            <span className={`pill ${summary?.plant_health === "GOOD" ? "good" : ""}`}>{isLoading ? "LOADING" : String(summary?.plant_health ?? "NO DATA")}</span>
            <span>{sourceMode === "LIVE_READ_ONLY" ? "LIVE · READ ONLY" : sourceMode === "HISTORICAL" ? "HISTORICAL" : sourceMode === "MIXED_DATA_BLOCKED" ? "MIXED DATA BLOCKED" : sourceMode === "SIMULATION" ? "SIMULATION" : "NO DATA"}</span>
          </div>
        </header>
        {summary?.safety_notice ? <p className="notice">{String(summary.safety_notice)}</p> : null}
        {error && <div className="error-state" role="alert"><strong>Workspace data unavailable.</strong> {error}<button className="secondary" onClick={() => window.location.reload()}>Retry</button></div>}
        {message && <p className="message" role="status">{message}<button className="message-dismiss" aria-label="Dismiss message" onClick={() => setMessage("")}>×</button></p>}
        <div className="content-area">{children}</div>
      </section>
    </div>
  );
}

export function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  return <WorkspaceDataProvider><ShellBody>{children}</ShellBody></WorkspaceDataProvider>;
}
