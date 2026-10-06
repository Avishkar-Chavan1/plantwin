"use client";

import { useEffect, useMemo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useDashboardData } from "@/components/use-dashboard-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Twin = { equipment: { tag: string; name: string }; mode: string; timestamp: string; health: { status: string; factors: unknown }; data_quality: unknown; prediction: { status: string; values: Record<string, number>; uncertainty: unknown }; history: Array<{ timestamp: string; mode: string; health_status: string; state: { physics?: { temperature_k?: number; conversion?: number; yield?: number; selectivity?: number } } }> };

export default function DigitalTwins() {
  const { summary, headers, isLoading } = useDashboardData();
  const equipmentId = (summary as { equipment?: { id: string } } | null)?.equipment?.id;
  const [twin, setTwin] = useState<Twin | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!equipmentId || !headers) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/api/v1/digital-twins/${equipmentId}/latest?limit=120`, { headers, signal: controller.signal }).then(async (response) => {
      if (response.status === 404) throw new Error("No synchronized twin state is available for this equipment yet.");
      if (!response.ok) throw new Error("Unable to load the latest twin state.");
      setTwin(await response.json() as Twin); setError("");
    }).catch((caught) => { if (caught.name !== "AbortError") { setTwin(null); setError(caught.message); } });
    return () => controller.abort();
  }, [equipmentId, headers]);
  const history = useMemo(() => twin?.history.slice().reverse().map((item) => ({ time: new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), temperature: item.state.physics?.temperature_k ? item.state.physics.temperature_k - 273.15 : null, yield: item.state.physics?.yield ? item.state.physics.yield * 100 : null })) ?? [], [twin]);
  if (isLoading) return <div className="console-loading">Loading twin state…</div>;
  if (!equipmentId) return <section className="panel empty-state"><h2>No equipment configured</h2><p>A digital twin becomes available after an equipment asset is configured.</p></section>;
  return <div className="digital-twins-view"><section className="panel"><div className="panel-title"><div><p className="eyebrow">SYNCHRONIZED STATE</p><h2>{twin?.equipment.tag ?? "Equipment twin"}</h2></div>{twin && <span className="pill">{twin.mode}</span>}</div>{error ? <div className="empty-state"><strong>{error}</strong><p>Twin state is not fabricated when the synchronization service has no record.</p></div> : twin ? <><div className="metrics"><Metric label="Twin health" value={twin.health.status} /><Metric label="Prediction" value={twin.prediction.status} /><Metric label="State samples" value={String(twin.history.length)} /><Metric label="Last update" value={new Date(twin.timestamp).toLocaleString()} /></div>{history.length ? <div className="plot twin-plot"><ResponsiveContainer width="100%" height={280}><LineChart data={history}><XAxis dataKey="time" tick={{ fill: "#86a0ae", fontSize: 11 }} minTickGap={30} /><YAxis yAxisId="left" tick={{ fill: "#86a0ae", fontSize: 11 }} /><YAxis yAxisId="right" orientation="right" tick={{ fill: "#86a0ae", fontSize: 11 }} /><Tooltip /><Line yAxisId="left" type="monotone" dataKey="temperature" stroke="#60d5c6" dot={false} name="Temperature °C" /><Line yAxisId="right" type="monotone" dataKey="yield" stroke="#f2b36d" dot={false} name="Yield %" /></LineChart></ResponsiveContainer></div> : <div className="empty-state">The latest twin state has no history samples.</div>}<details><summary>Data quality and prediction evidence</summary><pre className="data-json">{JSON.stringify({ data_quality: twin.data_quality, uncertainty: twin.prediction.uncertainty, factors: twin.health.factors }, null, 2)}</pre></details></> : <div className="empty-state">Loading synchronized twin state…</div>}</section></div>;
}

function Metric({ label, value }: { label: string; value: string }) { return <article className="metric"><p>{label}</p><h2 className="metric-text">{value}</h2></article>; }
