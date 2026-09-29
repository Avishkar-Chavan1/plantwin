from __future__ import annotations

import argparse

from packages.units import si_unit, to_si
from sqlalchemy import select

from apps.api.processtwin_api.auth import hash_password
from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import (
    Alert,
    AlertSeverity,
    Equipment,
    Organization,
    OrganizationMembership,
    QualityStatusName,
    RoleName,
    Sensor,
    SensorReading,
    User,
)

from .plant import SyntheticCSTRPlant

SENSOR_DEFINITIONS = [
    ("REACTOR_TEMPERATURE", "Reactor temperature", "degC", "temperature", 120.0, 260.0),
    ("REACTOR_PRESSURE", "Reactor pressure", "bar", "pressure", 0.0, 30.0),
    ("FEED_FLOW", "Feed flow", "m3/h", "flow", 0.0, 200.0),
    ("FEED_CONCENTRATION", "Feed A concentration", "mol/m3", "concentration", 0.0, 1000.0),
    ("COOLING_FLOW", "Cooling water flow", "m3/h", "flow", 0.0, 200.0),
    ("AGITATOR_SPEED", "Agitator speed", "rpm", "speed", 0.0, 500.0),
    ("CONVERSION", "Conversion", "%", "conversion", 0.0, 100.0),
    ("YIELD", "Product B yield", "%", "yield", 0.0, 100.0),
    ("SELECTIVITY", "Product B selectivity", "%", "selectivity", 0.0, 100.0),
    ("ENERGY_CONSUMPTION", "Cooling duty energy proxy", "kW", "energy", 0.0, 2000.0),
]


def seed(hours: float) -> None:
    settings = get_settings()
    if settings.is_production:
        raise RuntimeError("Synthetic demo data must never be seeded in production")
    if not settings.demo_email or not settings.demo_password:
        raise RuntimeError(
            "DEMO_EMAIL and DEMO_PASSWORD are required to seed the local demonstration"
        )
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        existing = session.scalar(
            select(Organization).where(Organization.name == "ProcessTwin Demonstration")
        )
        if existing is not None:
            print(f"Demo organization already exists: {existing.id}")
            return
        organization = Organization(name="ProcessTwin Demonstration")
        engineer = User(
            email=settings.demo_email.lower(), password_hash=hash_password(settings.demo_password)
        )
        session.add_all([organization, engineer])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=engineer.id, role=RoleName.OWNER
            )
        )
        from apps.api.processtwin_api.models import Plant

        plant = Plant(
            organization_id=organization.id,
            name="Demo Chemical Plant",
            location="Synthetic reference plant",
        )
        session.add(plant)
        session.flush()
        reactor = Equipment(
            organization_id=organization.id,
            plant_id=plant.id,
            tag="R-101",
            name="CSTR R-101",
            equipment_type="CSTR",
        )
        session.add(reactor)
        session.flush()
        sensors = {}
        for tag, name, unit, kind, minimum, maximum in SENSOR_DEFINITIONS:
            sensor = Sensor(
                organization_id=organization.id,
                equipment_id=reactor.id,
                name=name,
                tag=tag,
                unit=unit,
                measurement_type=kind,
                sampling_interval_s=300,
                min_valid_value=minimum,
                max_valid_value=maximum,
            )
            session.add(sensor)
            sensors[tag] = sensor
        session.flush()
        simulated = SyntheticCSTRPlant()
        for point in simulated.history(hours=hours):
            sensor = sensors[point.tag]
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
        # A deliberate cooling incident demonstrates anomaly/alert workflow without pretending to be a real failure.
        session.add(
            Alert(
                organization_id=organization.id,
                equipment_id=reactor.id,
                severity=AlertSeverity.WARNING,
                reason="Potential process anomaly: simulated cooling efficiency decreased by 10%; review the trend and model assumptions.",
            )
        )
        session.add(
            Alert(
                organization_id=organization.id,
                equipment_id=reactor.id,
                severity=AlertSeverity.INFO,
                reason="Demo data source is a synthetic CSTR simulator, not a real industrial connector.",
            )
        )
        session.commit()
        print(
            f"Seeded {hours:g} hours of simulated history for organization {organization.id}; user {engineer.email}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=168.0)
    arguments = parser.parse_args()
    seed(arguments.hours)
