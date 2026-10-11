"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "./auth-provider";
import { HealthBadge, ModeBadge, ReadOnlyBadge, normalizeMode } from "./status";
import { useWorkspaceData, WorkspaceDataProvider } from "./workspace-data";

const navigationGroups = [
  { label: "Operate", items: [
    { key: "dashboard", label: "Dashboard", href: "/dashboard" },
    { key: "plants", label: "Plants", href: "/plants" },
    { key: "equipment", label: "Equipment", href: "/equipment" },
    { key: "sensors", label: "Sensors", href: "/sensors" },
    { key: "alerts", label: "Alerts", href: "/alerts" },
  ] },
  { label: "Analyze", items: [
    { key: "digital-twins", label: "Digital twins", href: "/digital-twins" },
    { key: "simulations", label: "Simulations", href: "/simulations" },
    { key: "optimization", label: "Optimization", href: "/optimization" },
    { key: "recommendations", label: "Recommendations", href: "/recommendations" },
    { key: "models", label: "Models", href: "/models" },
  ] },
  { label: "Data", items: [
    { key: "data", label: "Historical data", href: "/data" },
    { key: "data-sources", label: "Data sources", href: "/data-sources" },
  ] },
  { label: "Administration", items: [
    { key: "settings", label: "Settings", href: "/settings" },
    { key: "audit-log", label: "Audit log", href: "/audit-log" },
  ] },
];
const navigation = navigationGroups.flatMap((group) => group.items);

function pageTitle(pathname: string) {
  const key = pathname.split("/").filter(Boolean)[0] ?? "dashboard";
  return navigation.find((entry) => entry.key === key)?.label ?? "Workspace";
}

function ShellBody({ children }: { children: React.ReactNode }) {
  const { logout } = useAuth();
  const { summary, isLoading, error, message, setMessage, refresh } = useWorkspaceData();
  const pathname = usePathname();
  const router = useRouter();
  const equipment = summary?.equipment as { tag?: string } | undefined;
  const mode = normalizeMode(summary?.source_mode);
  const dataState = String(summary?.data_state ?? "");
  const plant = summary?.plant as { name?: string } | null | undefined;

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  return (
    <div className="shell">
      <aside aria-label="Primary navigation">
        <Link className="logo" href="/dashboard" aria-label="ProcessTwin dashboard"><span aria-hidden="true">◈</span> ProcessTwin</Link>
        <nav>
          {navigationGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <p className="nav-heading">{group.label}</p>
              {group.items.map((entry) => (
                <Link key={entry.key} href={entry.href} className={pathname === entry.href || pathname.startsWith(`${entry.href}/`) ? "active" : ""} aria-current={pathname === entry.href ? "page" : undefined}>{entry.label}</Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="operator">
          <ReadOnlyBadge />
          <p>Advisory only. Recommendations require engineer review; no commands are sent to plant equipment.</p>
        </div>
        <button className="logout-btn" onClick={() => void handleLogout()}>Sign out</button>
      </aside>
      <section className="workspace">
        <header>
          <div>
            <p className="eyebrow">{plant?.name ?? "WORKSPACE"}{equipment?.tag ? ` / ${equipment.tag}` : ""}</p>
            <h1>{pageTitle(pathname)}</h1>
          </div>
          <div className="header-status">
            {isLoading ? <span className="muted">Loading…</span> : <>
              <ModeBadge mode={mode} />
              {summary?.plant_health ? <HealthBadge value={String(summary.plant_health)} /> : null}
            </>}
          </div>
        </header>
        {mode === "SIMULATION" && <p className="banner banner-sim" role="status"><strong>SIMULATION DATA.</strong> Values on this workspace come from a synthetic simulator, not from a plant.</p>}
        {mode === "LIVE_READ_ONLY" && (dataState === "STALE" || dataState === "PARTIALLY_STALE") && <p className="banner banner-warn" role="status"><strong>STALE DATA.</strong> One or more live inputs have not updated within their expected interval. Estimates that depend on them are withheld.</p>}
        {mode === "HISTORICAL" && <p className="banner banner-hist" role="status"><strong>HISTORICAL DATA.</strong> These are archival records, not the current plant state.</p>}
        {mode === "MIXED_DATA_BLOCKED" && <p className="banner banner-bad" role="alert"><strong>MIXED SOURCES.</strong> Live, historical and simulated readings exist for this equipment and are never combined. Resolve the sources to continue.</p>}
        {error && (
          <div className="error-state" role="alert">
            <strong>{summary ? "Live updates paused." : "Workspace data unavailable."}</strong> {error}
            <button className="secondary" onClick={() => void refresh()}>Retry</button>
          </div>
        )}
        {message && <p className="message" role="status">{message}<button className="message-dismiss" aria-label="Dismiss message" onClick={() => setMessage("")}>×</button></p>}
        <div className="content-area">{children}</div>
      </section>
    </div>
  );
}

export function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  return <WorkspaceDataProvider><ShellBody>{children}</ShellBody></WorkspaceDataProvider>;
}
