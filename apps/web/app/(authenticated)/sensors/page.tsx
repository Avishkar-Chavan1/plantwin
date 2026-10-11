"use client";

import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, formatNumber } from "@/components/status";
import { useApiResource } from "@/components/use-api-resource";

type Sensor = { id: string; equipment_id: string; tag: string; name: string; unit: string; measurement_type: string; enabled: boolean };
type Reading = { timestamp: string; value: number; unit: string; quality_status: string; source: string };

export default function Sensors() {
  const sensors = useApiResource<{ items: Sensor[] }>("/api/v1/sensors");
  const [sensorId, setSensorId] = useState("");
  const items = useMemo(() => sensors.data?.items ?? [], [sensors.data]);
  useEffect(() => { setSensorId((current) => current || items[0]?.id || ""); }, [items]);
  const readings = useApiResource<{ items: Reading[] }>(sensorId ? `/api/v1/sensors/${sensorId}/readings?limit=200` : null);
  const sensor = items.find((item) => item.id === sensorId);
  const points = useMemo(() => [...(readings.data?.items ?? [])].reverse().map((reading) => ({ time: new Date(reading.timestamp).getTime(), value: reading.value })), [readings.data]);
  const latest = readings.data?.items[0];

  if (sensors.loading) return <div className="console-loading" role="status">Loading sensors…</div>;
  return (
    <div className="sensors-view">
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">MEASUREMENT CATALOG</p><h2>Sensors</h2></div><span className="pill">{items.length} configured</span></div>
        {sensors.error && <div className="error-state" role="alert">{sensors.error}<button className="secondary" onClick={() => void sensors.reload()}>Retry</button></div>}
        {items.length === 0 && !sensors.error ? <div className="empty-state"><h2>No sensors configured</h2><p>Sensors are the mapped measurement points of your equipment. Import historical data or configure a read-only connector to populate them.</p></div> : (
          <div className="split-view">
            <div className="resource-list">
              {items.map((item) => (
                <button className={`resource-row ${item.id === sensorId ? "selected" : ""}`} key={item.id} onClick={() => setSensorId(item.id)}>
                  <strong>{item.tag}</strong><small>{item.name} · {item.unit} · {item.enabled ? "Enabled" : "Disabled"}</small>
                </button>
              ))}
            </div>
            <div className="resource-detail">
              {sensor && (
                <>
                  <p className="eyebrow">{sensor.tag} · {sensor.measurement_type}</p>
                  <h3>{sensor.name}</h3>
                  {readings.error ? <div className="error-state" role="alert">{readings.error}</div>
                    : readings.loading ? <div className="empty-state">Loading readings…</div>
                    : points.length === 0 ? <div className="empty-state"><h2>No data available</h2><p>No readings are stored for this sensor.</p></div> : (
                      <div className="plot" role="img" aria-label={`${sensor.name} readings, ${points.length} samples`}>
                        <ResponsiveContainer width="100%" height={280}>
                          <LineChart data={points}>
                            <CartesianGrid stroke="#17384e" strokeDasharray="3 3" />
                            <XAxis dataKey="time" type="number" scale="time" domain={["dataMin", "dataMax"]} tick={{ fill: "#86a0ae", fontSize: 11 }} minTickGap={36} tickFormatter={(value) => new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} />
                            <YAxis tick={{ fill: "#86a0ae", fontSize: 11 }} domain={["auto", "auto"]} width={54} />
                            <Tooltip contentStyle={{ background: "#0b2131", border: "1px solid #31536a", color: "#e7f1f5" }} labelFormatter={(label) => new Date(Number(label)).toLocaleString()} formatter={(value) => [`${formatNumber(Number(value), 2)} ${sensor.unit}`, sensor.name]} />
                            <Line type="monotone" dataKey="value" stroke="#60d5c6" dot={false} strokeWidth={2} isAnimationActive={false} />
                          </LineChart>
                        </ResponsiveContainer>
                        <small className="chart-caption">{points.length} readings · {sensor.unit} · latest source <Badge tone="idle">{latest?.source ?? "—"}</Badge> · quality {latest?.quality_status ?? "—"}</small>
                      </div>
                    )}
                </>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
