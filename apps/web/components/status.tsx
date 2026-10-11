import type { ReactNode } from "react";

import { type SourceMode, modeInfo } from "./format";

export { formatAge, formatNumber, formatTimestamp, modeInfo, normalizeMode } from "./format";
export type { SourceMode } from "./format";

type Tone = "live" | "sim" | "hist" | "warn" | "bad" | "idle";

export function Badge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return <span className={`badge badge-${tone}`} title={title}>{children}</span>;
}

export function ModeBadge({ mode }: { mode: SourceMode }) {
  const info = modeInfo(mode);
  return <Badge tone={info.tone} title={info.description}>{info.label}</Badge>;
}

export function ReadOnlyBadge() {
  return <Badge tone="idle" title="ProcessTwin has no write path to PLC, DCS or SCADA systems.">READ-ONLY</Badge>;
}

/** Map a backend health/data-state string onto a badge so every state has one consistent look. */
export function HealthBadge({ value }: { value: string | null | undefined }) {
  const state = (value ?? "NO_DATA").toUpperCase();
  const table: Record<string, { tone: Tone; label: string }> = {
    HEALTHY: { tone: "live", label: "HEALTHY" },
    DEGRADED: { tone: "warn", label: "DEGRADED" },
    STALE_DATA: { tone: "warn", label: "STALE DATA" },
    STALE: { tone: "warn", label: "STALE DATA" },
    PARTIALLY_STALE: { tone: "warn", label: "PARTLY STALE" },
    INCOMPLETE: { tone: "warn", label: "INCOMPLETE" },
    INSUFFICIENT_EVIDENCE: { tone: "idle", label: "INSUFFICIENT EVIDENCE" },
    WAITING_FOR_SYNCHRONIZED_TWIN: { tone: "idle", label: "WAITING FOR TWIN" },
    MIXED_DATA_BLOCKED: { tone: "bad", label: "BLOCKED" },
    BLOCKED: { tone: "bad", label: "BLOCKED" },
    ERROR: { tone: "bad", label: "ERROR" },
    CONNECTED: { tone: "live", label: "CONNECTED" },
    DISCONNECTED: { tone: "bad", label: "OFFLINE" },
    OK: { tone: "live", label: "OK" },
    NO_DATA: { tone: "idle", label: "NO DATA" },
    SIMULATION: { tone: "sim", label: "SIMULATION" },
  };
  const entry = table[state] ?? { tone: "idle" as Tone, label: state.replaceAll("_", " ") };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}
