"use client";

import { useEffect, useState } from "react";
import { useApiResource } from "@/components/use-api-resource";
import { useWorkspaceData } from "@/components/workspace-data";

type PlantDetail = { id: string; name: string; location: string | null; equipment: Array<{ id: string; tag: string; name: string; type: string }> };

export default function Plants() {
  const { plants, isLoading } = useWorkspaceData();
  const [selectedId, setSelectedId] = useState("");
  useEffect(() => { setSelectedId((current) => current || plants[0]?.id || ""); }, [plants]);
  const detail = useApiResource<PlantDetail>(selectedId ? `/api/v1/plants/${selectedId}` : null);

  if (isLoading) return <div className="console-loading" role="status">Loading plants…</div>;
  if (!plants.length) return <section className="panel empty-state"><h2>No plants configured</h2><p>No plant exists for this organization yet. Plants are created by an administrator or during onboarding before equipment and data can be associated.</p></section>;
  return (
    <div className="plants-view">
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">ASSETS</p><h2>Plants</h2></div><span className="pill">{plants.length} configured</span></div>
        <div className="split-view">
          <div className="resource-list">
            {plants.map((plant) => (
              <button className={`resource-row ${plant.id === selectedId ? "selected" : ""}`} key={plant.id} onClick={() => setSelectedId(plant.id)}>
                <strong>{plant.name}</strong><small>{plant.location ?? "Location not recorded"}</small>
              </button>
            ))}
          </div>
          <div className="resource-detail">
            {detail.error && <div className="error-state" role="alert">{detail.error}<button className="secondary" onClick={() => void detail.reload()}>Retry</button></div>}
            {detail.loading ? <div className="empty-state">Loading plant…</div> : detail.data && (
              <>
                <p className="eyebrow">PLANT DETAIL</p>
                <h3>{detail.data.name}</h3>
                <p className="muted">{detail.data.location ?? "Location not recorded"}</p>
                <h4>Equipment ({detail.data.equipment.length})</h4>
                {detail.data.equipment.length ? <ul>{detail.data.equipment.map((item) => <li key={item.id}><strong>{item.tag}</strong> · {item.name}<small>{item.type}</small></li>)}</ul> : <p className="empty-state">No equipment is associated with this plant.</p>}
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
