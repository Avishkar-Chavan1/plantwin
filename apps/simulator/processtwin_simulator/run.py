from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from packages.units import si_unit, to_si
from sqlalchemy import select

from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import Organization, QualityStatusName, Sensor, SensorReading

from .plant import SyntheticCSTRPlant


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
        count = 0
        for point in plant.history(hours=1 / 12, interval_s=300, start=datetime.now(UTC)):
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
        session.commit()
        return count


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
