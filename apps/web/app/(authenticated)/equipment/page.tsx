"use client";

import { useEffect, useState } from "react";
import { useDashboardData } from "@/components/use-dashboard-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Equipment = { id: string; tag: string; name: string; type: string };
type PlantDetail = { name: string; equipment: Equipment[] };

export default function EquipmentPage() {
  const { plants, headers, isLoading } = useDashboardData();
  const [plantId, setPlantId] = useState("");
  const [detail, setDetail] = useState<PlantDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { setPlantId((current) => current || plants[0]?.id || ""); }, [plants]);
  useEffect(() => {
    if (!plantId || !headers) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/api/v1/plants/${plantId}`, { headers, signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("Unable to load equipment for this plant.");
      setDetail(await response.json() as PlantDetail); setError("");
    }).catch((caught) => { if (caught.name !== "AbortError") setError(caught.message); });
    return () => controller.abort();
  }, [headers, plantId]);
  if (isLoading) return <div className="console-loading">Loading equipment…</div>;
  return <div className="equipment-view"><section className="panel"><div className="panel-title"><div><p className="eyebrow">ASSET REGISTER</p><h2>Equipment</h2></div><label className="compact-control">Plant<select value={plantId} onChange={(event) => setPlantId(event.target.value)}>{plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}</select></label></div>{error && <p className="error-state" role="alert">{error}</p>}{detail?.equipment.length ? <div className="table-wrap"><table><thead><tr><th>Tag</th><th>Name</th><th>Type</th><th>Plant</th></tr></thead><tbody>{detail.equipment.map((item) => <tr key={item.id}><td><strong>{item.tag}</strong></td><td>{item.name}</td><td>{item.type}</td><td>{detail.name}</td></tr>)}</tbody></table></div> : <div className="empty-state">{detail ? "No equipment is configured for this plant." : "Select a plant to inspect its equipment."}</div>}</section></div>;
}
