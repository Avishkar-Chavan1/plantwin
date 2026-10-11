"use client";

import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { HealthBadge, ModeBadge, formatTimestamp, normalizeMode } from "@/components/status";
import { useApiResource } from "@/components/use-api-resource";
import { useWorkspaceData } from "@/components/workspace-data";

type Twin = {
  equipment: { tag: string; name: string };
  mode: string;
  timestamp: string;
  health: { status: string; factors: unknown };
  data_quality: unknown;
  prediction: { status: string; values: Record<string, number>; uncertainty: unknown };
  history: Array<{ timestamp: string; state: { physics?: { temperature_k?: number; yield?: number } } }>;
};

export default function DigitalTwins() {
  const { summary, isLoading } = useWorkspaceData();
  const equipmentId = (summary as { equipment?: { id: string } | null } | null)?.equipment?.id;
  const twin = useApiResource<Twin>(equipmentId ? `/api/v1/digital-twins/${equipmentId}/latest?limit=120` : null, { pollMs: 30_000 });
  const history = useMemo(
    () => (twin.data?.history ?? []).slice().reverse().map((item) => ({
      time: new Date(item.timestamp).getTime(),
      temperature: item.state.physics?.temperature_k ? item.state.physics.temperature_k - 273.15 : null,
      yield: item.state.physics?.yield !== undefined ? item.state.physics.yield * 100 : null,
    })),
    [twin.data],
  );

  if (isLoading) return <div className="console-loading" role="status">Loading twin state…</div>;
  if (!equipmentId) return <section className="panel empty-state"><h2>No equipment configured</h2><p>A digital twin becomes available after an equipment asset is configured.</p></section>;
  const data = twin.data;
  return (
    <div className="digital-twins-view">
      <section className="panel">
        <div className="panel-title">
          <div><p className="eyebrow">SYNCHRONIZED STATE · MODEL ESTIMATES</p><h2>{data?.equipment.tag ?? "Equipment twin"}</h2></div>
          {data && <ModeBadge mode={normalizeMode(data.mode)} />}
        </div>
        {twin.loading ? <div className="empty-state">Loading synchronized twin state…</div>
          : twin.error ? <div className="empty-state"><h2>No twin state available</h2><p>{twin.error}</p><p>Twin values are never fabricated: they appear only after required inputs are mapped and reporting.</p></div>
          : data && (
            <>
              <div className="kpi-row compact">
                <article className="kpi"><p>Twin health</p><div className="kpi-value"><HealthBadge value={data.health.status} /></div></article>
                <article className="kpi"><p>Prediction</p><div className="kpi-value kpi-text">{data.prediction.status.replaceAll("_", " ")}</div></article>
                <article className="kpi"><p>State samples</p><div className="kpi-value kpi-number">{data.history.length}</div></article>
                <article className="kpi"><p>Last update</p><div className="kpi-value kpi-text">{formatTimestamp(data.timestamp)}</div></article>
              </div>
              {history.some((point) => point.temperature !== null || point.yield !== null) ? (
                <div className="plot twin-plot" role="img" aria-label="Twin temperature and yield estimates over time">
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={history}>
                      <CartesianGrid stroke="#17384e" strokeDasharray="3 3" />
                      <XAxis dataKey="time" type="number" scale="time" domain={["dataMin", "dataMax"]} tick={{ fill: "#86a0ae", fontSize: 11 }} minTickGap={36} tickFormatter={(value) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} />
                      <YAxis yAxisId="left" tick={{ fill: "#86a0ae", fontSize: 11 }} width={50} />
                      <YAxis yAxisId="right" orientation="right" tick={{ fill: "#86a0ae", fontSize: 11 }} width={50} />
                      <Tooltip contentStyle={{ background: "#0b2131", border: "1px solid #31536a", color: "#e7f1f5" }} labelFormatter={(label) => new Date(Number(label)).toLocaleString()} />
                      <Line yAxisId="left" type="monotone" dataKey="temperature" stroke="#60d5c6" dot={false} name="Estimated temperature °C" isAnimationActive={false} connectNulls />
                      <Line yAxisId="right" type="monotone" dataKey="yield" stroke="#f2b36d" dot={false} name="Estimated yield %" isAnimationActive={false} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                  <small className="chart-caption">Model estimates computed from stored inputs; not direct measurements.</small>
                </div>
              ) : <div className="empty-state">No model estimates are available in the stored twin history.</div>}
              <details><summary>Data quality and prediction evidence</summary><pre className="data-json">{JSON.stringify({ data_quality: data.data_quality, uncertainty: data.prediction.uncertainty, factors: data.health.factors }, null, 2)}</pre></details>
            </>
          )}
      </section>
    </div>
  );
}
