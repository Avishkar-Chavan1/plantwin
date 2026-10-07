"use client";

import { useEffect, useMemo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Reading = { timestamp: string; value: number; unit: string; quality_status: string; source: string };
type Sensor = { id: string; equipment_id: string; tag: string; name: string; unit: string; measurement_type: string };
type Summary = {
  equipment?: { id: string; tag: string; name: string };
  source_mode?: string;
  plant_health?: string;
  measurements?: Record<string, Reading>;
  twin?: { conversion?: Reading; yield?: Reading; selectivity?: Reading; heat_removal?: Reading; temperature?: Reading };
  active_alerts?: number;
  safety_notice?: string;
};

interface ProcessTwinConsoleProps {
  token: string;
  organization: string;
  headers: Record<string, string>;
  summary: Summary | null;
  setMessage: (message: string) => void;
}

function formatValue(value: number | null | undefined, digits = 1) {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

export function ProcessTwinConsole({ token, organization, headers, summary, setMessage }: ProcessTwinConsoleProps) {
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [readings, setReadings] = useState<Record<string, Reading[]>>({});
  const [selectedSensorId, setSelectedSensorId] = useState("");
  const [trendLoading, setTrendLoading] = useState(false);
  const equipmentId = summary?.equipment?.id;

  useEffect(() => {
    if (!equipmentId || !token || !organization) return;
    const controller = new AbortController();
    async function loadTrend() {
      setTrendLoading(true);
      try {
        const sensorResponse = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1/sensors`, { headers, signal: controller.signal });
        if (!sensorResponse.ok) return;
        const allSensors = (await sensorResponse.json() as { items?: Sensor[] }).items ?? [];
        const equipmentSensors = allSensors.filter((sensor) => sensor.equipment_id === equipmentId).slice(0, 4);
        setSensors(equipmentSensors);
        setSelectedSensorId((current) => equipmentSensors.some((sensor) => sensor.id === current) ? current : equipmentSensors[0]?.id ?? "");
        const responses = await Promise.all(equipmentSensors.map(async (sensor) => {
          const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1/sensors/${sensor.id}/readings?limit=120`, { headers, signal: controller.signal });
          if (!response.ok) return [sensor.id, []] as const;
          const body = await response.json() as { items?: Reading[] };
          return [sensor.id, (body.items ?? []).reverse()] as const;
        }));
        if (!controller.signal.aborted) setReadings(Object.fromEntries(responses));
      } catch (error) {
        if ((error as Error).name !== "AbortError") setMessage("Recent sensor history could not be loaded; latest values remain available.");
      } finally {
        if (!controller.signal.aborted) setTrendLoading(false);
      }
    }
    void loadTrend();
    return () => controller.abort();
  }, [equipmentId, headers, organization, setMessage, token]);

  const selectedSensor = sensors.find((sensor) => sensor.id === selectedSensorId);
  const trend = useMemo(() => (selectedSensor ? (readings[selectedSensor.id] ?? []).map((reading) => ({
    time: new Date(reading.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    value: reading.value,
    quality: reading.quality_status,
  })) : []), [readings, selectedSensor]);
  const measurements = summary?.measurements ?? {};
  const twin = summary?.twin;

  if (!summary?.equipment) return <EmptyState title="No equipment configured" body="Add an equipment asset to this organization before opening a process twin." />;

  return <>
    <div className="metrics">
      <Metric label="Reactor temperature" value={measurements.REACTOR_TEMPERATURE?.value ?? twin?.temperature?.value} unit={measurements.REACTOR_TEMPERATURE?.unit ?? "°C"} status={measurements.REACTOR_TEMPERATURE?.source ?? twin?.temperature?.source} />
      <Metric label="Reactor pressure" value={measurements.REACTOR_PRESSURE?.value} unit={measurements.REACTOR_PRESSURE?.unit ?? "bar"} status={measurements.REACTOR_PRESSURE?.source} />
      <Metric label="Feed flow" value={measurements.FEED_FLOW?.value} unit={measurements.FEED_FLOW?.unit ?? "m³/h"} status={measurements.FEED_FLOW?.source} />
      <Metric label="Predicted yield" value={twin?.yield?.value} unit="%" status={twin?.yield?.source ?? "ESTIMATED"} />
    </div>

    <section className="grid">
      <article className="panel trend">
        <div className="panel-title"><div><p className="eyebrow">MEASURED SENSOR HISTORY</p><h2>{selectedSensor?.name ?? "No sensor history"}</h2></div>
          {sensors.length > 0 && <label className="compact-control"><span className="sr-only">Trend sensor</span><select value={selectedSensorId} onChange={(event) => setSelectedSensorId(event.target.value)}>{sensors.map((sensor) => <option key={sensor.id} value={sensor.id}>{sensor.tag}</option>)}</select></label>}
        </div>
        {trendLoading ? <div className="empty-state">Loading measured history…</div> : trend.length === 0 ? <div className="empty-state">No readings are available for this equipment. Connect a read-only source or import historical data to see a trend.</div> : <div className="plot" aria-label={`${selectedSensor?.name ?? "Sensor"} measured trend`}><ResponsiveContainer width="100%" height={260}><LineChart data={trend}><XAxis dataKey="time" tick={{ fill: "#86a0ae", fontSize: 11 }} minTickGap={32} /><YAxis tick={{ fill: "#86a0ae", fontSize: 11 }} width={50} domain={["auto", "auto"]} /><Tooltip contentStyle={{ background: "#0b2131", border: "1px solid #31536a", color: "#e7f1f5" }} /><Line type="monotone" dataKey="value" stroke="#60d5c6" dot={false} strokeWidth={2} name={selectedSensor?.unit ?? "value"} /></LineChart></ResponsiveContainer><small className="chart-caption">Historical/read-only readings only · {selectedSensor?.unit ?? "engineering units"}</small></div>}
      </article>
      <article className="panel reactor">
        <p className="eyebrow">EQUIPMENT STATE</p><h2>{summary.equipment.tag}</h2><p className="muted">{summary.equipment.name}</p>
        <dl className="state-list"><div><dt>Source mode</dt><dd>{summary.source_mode ?? "NO_DATA"}</dd></div><div><dt>Health</dt><dd>{summary.plant_health ?? "NO_DATA"}</dd></div><div><dt>Active alerts</dt><dd>{summary.active_alerts ?? 0}</dd></div><div><dt>Conversion</dt><dd>{formatValue(twin?.conversion?.value)}%</dd></div><div><dt>Selectivity</dt><dd>{formatValue(twin?.selectivity?.value)}%</dd></div></dl>
        <p className="notice compact">Advisory/read-only boundary: ProcessTwin does not send plant-control commands.</p>
      </article>
    </section>
  </>;
}

function Metric({ label, value, unit, status }: { label: string; value?: number; unit: string; status?: string }) {
  return <article className="metric"><p>{label}</p><h2>{formatValue(value)}<small>{unit}</small></h2><span>{status ?? "NO DATA"}</span></article>;
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return <section className="panel empty-state"><h2>{title}</h2><p>{body}</p></section>;
}
