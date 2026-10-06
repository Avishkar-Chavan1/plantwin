"use client";

import { useEffect, useMemo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useDashboardData } from "@/components/use-dashboard-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Sensor = { id: string; equipment_id: string; tag: string; name: string; unit: string; measurement_type: string; enabled: boolean };
type Reading = { timestamp: string; value: number; unit: string; quality_status: string; source: string };

export default function Sensors() {
  const { headers, isLoading } = useDashboardData();
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [sensorId, setSensorId] = useState("");
  const [readings, setReadings] = useState<Reading[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!headers) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/api/v1/sensors`, { headers, signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("Unable to load sensors.");
      const items = (await response.json() as { items?: Sensor[] }).items ?? [];
      setSensors(items); setSensorId((current) => current || items[0]?.id || "");
    }).catch((caught) => { if (caught.name !== "AbortError") setError(caught.message); }).finally(() => setLoading(false));
    return () => controller.abort();
  }, [headers]);
  useEffect(() => {
    if (!headers || !sensorId) { setReadings([]); return; }
    const controller = new AbortController(); setLoading(true);
    void fetch(`${apiUrl}/api/v1/sensors/${sensorId}/readings?limit=200`, { headers, signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("Unable to load sensor readings.");
      setReadings(((await response.json() as { items?: Reading[] }).items ?? []).reverse()); setError("");
    }).catch((caught) => { if (caught.name !== "AbortError") setError(caught.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [headers, sensorId]);
  const sensor = sensors.find((item) => item.id === sensorId);
  const chartData = useMemo(() => readings.map((reading) => ({ time: new Date(reading.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), value: reading.value })), [readings]);
  if (isLoading || (loading && sensors.length === 0)) return <div className="console-loading">Loading sensors…</div>;
  return <div className="sensors-view"><section className="panel"><div className="panel-title"><div><p className="eyebrow">MEASUREMENT CATALOG</p><h2>Sensors</h2></div><span className="pill">{sensors.length} configured</span></div>{error && <p className="error-state" role="alert">{error}</p>}{sensors.length === 0 ? <div className="empty-state">No sensors are configured for this organization.</div> : <div className="split-view"><div className="resource-list">{sensors.map((item) => <button className={`resource-row ${item.id === sensorId ? "selected" : ""}`} key={item.id} onClick={() => setSensorId(item.id)}><strong>{item.tag}</strong><small>{item.name} · {item.unit} · {item.enabled ? "Enabled" : "Disabled"}</small></button>)}</div><div className="resource-detail">{sensor && <><p className="eyebrow">{sensor.tag} · {sensor.measurement_type}</p><h3>{sensor.name}</h3>{loading ? <div className="empty-state">Loading readings…</div> : chartData.length === 0 ? <div className="empty-state">No readings are available for this sensor.</div> : <div className="plot"><ResponsiveContainer width="100%" height={280}><LineChart data={chartData}><XAxis dataKey="time" tick={{ fill: "#86a0ae", fontSize: 11 }} minTickGap={30} /><YAxis tick={{ fill: "#86a0ae", fontSize: 11 }} domain={["auto", "auto"]} /><Tooltip /><Line type="monotone" dataKey="value" stroke="#60d5c6" dot={false} strokeWidth={2} /></LineChart></ResponsiveContainer><p className="chart-caption">{readings.length} readings · latest source: {readings.at(-1)?.source ?? "—"} · {sensor.unit}</p></div>}</>}</div></div>}</section></div>;
}
