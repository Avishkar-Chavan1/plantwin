from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from uuid import UUID

from packages.units import si_unit, to_si
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import (
    DataSource,
    Organization,
    QualityStatusName,
    Sensor,
    SensorReading,
)
from apps.api.processtwin_api.realtime import LiveIngestionGateway

from .plant import SyntheticCSTRPlant

SIMULATOR_SOURCE_NAME = "Synthetic CSTR simulator"


def append_live_sample() -> int:
    """Append one explicitly simulated instantaneous sample for an already-seeded demo tenant."""
    settings = get_settings()
    if settings.is_production:
        raise RuntimeError("The synthetic simulator is disabled in production")
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        organization = session.scalar(
            select(Organization).where(Organization.name == "ProcessTwin Demonstration")
        )
        if organization is None:
            return 0
        sensors = {
            sensor.tag: sensor
            for sensor in session.scalars(
                select(Sensor).where(Sensor.organization_id == organization.id)
            )
        }
        plant = SyntheticCSTRPlant(seed=int(datetime.now(UTC).timestamp()))
        now = datetime.now(UTC)
        count = 0
        for point in plant.history(hours=1 / 12, interval_s=300, start=now):
            # The trajectory window is inclusive of the interval end; never persist
            # future-dated samples: every stored reading must already have happened.
            if point.timestamp > now:
                continue
            sensor = sensors.get(point.tag)
            if sensor is not None:
                session.add(
                    SensorReading(
                        organization_id=organization.id,
                        sensor_id=sensor.id,
                        timestamp=point.timestamp,
                        value=point.value,
                        unit=point.unit,
                        original_value=point.value,
                        original_unit=point.unit,
                        normalized_value=to_si(point.value, point.unit),
                        normalized_unit=si_unit(point.unit),
                        quality_status=QualityStatusName(point.quality),
                        quality_reasons=[f"SYNTHETIC_{point.quality}_READING"],
                        source="SIMULATED",
                    )
                )
                count += 1
        source = session.scalar(
            select(DataSource).where(
                DataSource.organization_id == organization.id,
                DataSource.name == SIMULATOR_SOURCE_NAME,
            )
        )
        if source is not None and count:
            # The simulator is genuinely the source of these readings; report real health.
            source.status = "CONNECTED"
            source.last_success_at = now
            source.last_message_at = now
            source.last_error = None
            source.freshness_s = 0.0
        session.commit()
        if count:
            _synchronize_equipment(session, organization.id, now)
        return count


def _synchronize_equipment(session: Session, organization_id: UUID, now: datetime) -> None:
    """Persist a twin state per equipment from the latest stored readings."""
    from apps.api.processtwin_api.models import Equipment

    gateway = LiveIngestionGateway()
    equipment_ids = session.scalars(
        select(Equipment.id).where(Equipment.organization_id == organization_id)
    ).all()
    for equipment_id in equipment_ids:
        gateway.synchronize(session, equipment_id, organization_id, now=now)
    session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    args = parser.parse_args()
    if args.forever:
        while True:
            append_live_sample()
            time.sleep(300)
    else:
        for _ in range(args.steps):
            print(f"Generated {append_live_sample()} simulated readings")
