"use client";

import { useEffect, useState } from "react";
import { useDashboardData } from "@/components/use-dashboard-data";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type PlantDetail = { id: string; name: string; location: string | null; equipment: Array<{ id: string; tag: string; name: string; type: string }> };

export default function Plants() {
  const { plants, headers, isLoading } = useDashboardData();
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<PlantDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { setSelectedId((current) => current || plants[0]?.id || ""); }, [plants]);
  useEffect(() => {
    if (!selectedId || !headers) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/api/v1/plants/${selectedId}`, { headers, signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("Unable to load plant details.");
      setDetail(await response.json() as PlantDetail); setError("");
    }).catch((caught) => { if (caught.name !== "AbortError") setError(caught.message); });
    return () => controller.abort();
  }, [headers, selectedId]);
  if (isLoading) return <div className="console-loading">Loading plants…</div>;
  if (!plants.length) return <section className="panel empty-state"><h2>No plants configured</h2><p>Create a plant in the tenant database before associating equipment or data.</p></section>;
  return <div className="plants-view"><section className="panel"><div className="panel-title"><div><p className="eyebrow">TENANT ASSETS</p><h2>Plants</h2></div><span className="pill">{plants.length} configured</span></div><div className="split-view"><div className="resource-list">{plants.map((plant) => <button className={`resource-row ${plant.id === selectedId ? "selected" : ""}`} key={plant.id} onClick={() => setSelectedId(plant.id)}><strong>{plant.name}</strong><small>{plant.location ?? "Location not recorded"}</small></button>)}</div>{detail && <div className="resource-detail"><p className="eyebrow">PLANT DETAIL</p><h3>{detail.name}</h3><p className="muted">{detail.location ?? "Location not recorded"}</p><h4>Equipment ({detail.equipment.length})</h4>{detail.equipment.length ? <ul>{detail.equipment.map((item) => <li key={item.id}><strong>{item.tag}</strong> · {item.name}<small>{item.type}</small></li>)}</ul> : <p className="empty-state">No equipment is associated with this plant.</p>}</div>}</div>{error && <p className="error-state" role="alert">{error}</p>}</section></div>;
}
