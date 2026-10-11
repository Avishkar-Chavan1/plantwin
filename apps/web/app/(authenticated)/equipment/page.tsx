"use client";

import { useEffect, useState } from "react";
import { useApiResource } from "@/components/use-api-resource";
import { useWorkspaceData } from "@/components/workspace-data";

type Equipment = { id: string; tag: string; name: string; type: string };
type PlantDetail = { name: string; equipment: Equipment[] };

export default function EquipmentPage() {
  const { plants, isLoading } = useWorkspaceData();
  const [plantId, setPlantId] = useState("");
  useEffect(() => { setPlantId((current) => current || plants[0]?.id || ""); }, [plants]);
  const detail = useApiResource<PlantDetail>(plantId ? `/api/v1/plants/${plantId}` : null);

  if (isLoading) return <div className="console-loading" role="status">Loading equipment…</div>;
  if (!plants.length) return <section className="panel empty-state"><h2>No plants configured</h2><p>Equipment belongs to a plant. No plant exists for this organization yet.</p></section>;
  return (
    <div className="equipment-view">
      <section className="panel">
        <div className="panel-title">
          <div><p className="eyebrow">ASSET REGISTER</p><h2>Equipment</h2></div>
          <label className="compact-control">Plant
            <select value={plantId} onChange={(event) => setPlantId(event.target.value)}>
              {plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
            </select>
          </label>
        </div>
        {detail.error && <div className="error-state" role="alert">{detail.error}<button className="secondary" onClick={() => void detail.reload()}>Retry</button></div>}
        {detail.loading ? <div className="empty-state">Loading equipment…</div>
          : detail.data?.equipment.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Tag</th><th>Name</th><th>Type</th><th>Plant</th></tr></thead>
                <tbody>{detail.data.equipment.map((item) => <tr key={item.id}><td><strong>{item.tag}</strong></td><td>{item.name}</td><td>{item.type}</td><td>{detail.data?.name}</td></tr>)}</tbody>
              </table>
            </div>
          ) : !detail.error ? <div className="empty-state">No equipment is configured for this plant.</div> : null}
      </section>
    </div>
  );
}
