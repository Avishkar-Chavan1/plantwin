from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import UUID

from packages.units import si_unit, to_si
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.processtwin_api.auth import hash_password
from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import (
    Alert,
    AlertSeverity,
    DataSource,
    Equipment,
    Organization,
    OrganizationMembership,
    Plant,
    QualityStatusName,
    RoleName,
    Sensor,
    SensorReading,
    SourceTagMapping,
    User,
)
from apps.api.processtwin_api.realtime import LiveIngestionGateway

from .plant import SyntheticCSTRPlant

SENSOR_DEFINITIONS = [
    ("REACTOR_TEMPERATURE", "Reactor temperature", "degC", "temperature", 120.0, 260.0),
    ("REACTOR_PRESSURE", "Reactor pressure", "bar", "pressure", 0.0, 30.0),
    ("FEED_FLOW", "Feed flow", "m3/h", "flow", 0.0, 200.0),
    ("FEED_TEMPERATURE", "Feed temperature", "degC", "temperature", 100.0, 300.0),
    ("FEED_CONCENTRATION", "Feed A concentration", "mol/m3", "concentration", 0.0, 1000.0),
    ("COOLING_FLOW", "Cooling water flow", "m3/h", "flow", 0.0, 200.0),
    ("COOLING_TEMPERATURE", "Cooling water temperature", "degC", "temperature", 5.0, 100.0),
    ("AGITATOR_SPEED", "Agitator speed", "rpm", "speed", 0.0, 500.0),
    ("CONVERSION", "Conversion", "%", "conversion", 0.0, 100.0),
    ("YIELD", "Product B yield", "%", "yield", 0.0, 100.0),
    ("SELECTIVITY", "Product B selectivity", "%", "selectivity", 0.0, 100.0),
    ("ENERGY_CONSUMPTION", "Cooling duty energy proxy", "kW", "energy", 0.0, 2000.0),
]

# Canonical names consumed by the digital-twin synchronization service
# (apps/api/processtwin_api/realtime.py). The synthetic simulator is the real
# source of these readings, so the mappings describe an actual data path.
CANONICAL_NAMES = {
    "REACTOR_TEMPERATURE": "reactor.temperature",
    "REACTOR_PRESSURE": "reactor.pressure",
    "FEED_FLOW": "reactor.feed_flow",
    "FEED_TEMPERATURE": "reactor.feed_temperature",
    "FEED_CONCENTRATION": "reactor.feed_concentration",
    "COOLING_FLOW": "reactor.cooling_flow",
    "COOLING_TEMPERATURE": "reactor.cooling_temperature",
    "AGITATOR_SPEED": "agitator.speed",
    "CONVERSION": "reactor.conversion",
    "YIELD": "reactor.yield",
    "SELECTIVITY": "reactor.selectivity",
    "ENERGY_CONSUMPTION": "reactor.energy_consumption",
}

SIMULATOR_SOURCE_NAME = "Synthetic CSTR simulator"


def _ensure_simulator_source(
    session: Session, organization_id: UUID, plant_id: UUID | None
) -> DataSource:
    """Create (or return) the read-only demo simulator data source with tag mappings."""
    source = session.scalar(
        select(DataSource).where(
            DataSource.organization_id == organization_id,
            DataSource.name == SIMULATOR_SOURCE_NAME,
        )
    )
    now = datetime.now(UTC)
    if source is None:
        source = DataSource(
            organization_id=organization_id,
            plant_id=plant_id,
            name=SIMULATOR_SOURCE_NAME,
            source_type="SIMULATOR",
            endpoint="simulator://synthetic-cstr",
            read_only=True,
            configuration={"description": "Deterministic synthetic CSTR readings (not a live plant)"},
            status="CONNECTED",
            last_success_at=now,
            last_message_at=now,
        )
        session.add(source)
        session.flush()
    sensors = {
        sensor.tag: sensor
        for sensor in session.scalars(
            select(Sensor).where(Sensor.organization_id == organization_id)
        )
    }
    existing_keys = {
        key
        for key in session.scalars(
            select(SourceTagMapping.source_key).where(
                SourceTagMapping.organization_id == organization_id,
                SourceTagMapping.data_source_id == source.id,
            )
        )
    }
    for tag, canonical in CANONICAL_NAMES.items():
        sensor = sensors.get(tag)
        if sensor is None or tag in existing_keys:
            continue
        session.add(
            SourceTagMapping(
                organization_id=organization_id,
                data_source_id=source.id,
                sensor_id=sensor.id,
                source_key=tag,
                canonical_name=canonical,
                source_unit=sensor.unit,
            )
        )
    session.flush()
    return source


def _synchronize_equipment(session: Session, organization_id: UUID) -> None:
    """Persist one twin state per equipment from the latest stored readings."""
    gateway = LiveIngestionGateway()
    equipment_ids = session.scalars(
        select(Equipment.id).where(Equipment.organization_id == organization_id)
    ).all()
    for equipment_id in equipment_ids:
        gateway.synchronize(session, equipment_id, organization_id)
    session.commit()


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
            # Idempotent upgrade path: an older demo database still receives any
            # sensor/source/mapping added later, then an initial twin synchronization.
            print(f"Demo organization already exists: {existing.id}")
            plant = session.scalar(
                select(Plant).where(Plant.organization_id == existing.id)
            )
            _ensure_sensors(session, existing.id)
            _ensure_simulator_source(session, existing.id, plant.id if plant else None)
            _synchronize_equipment(session, existing.id)
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
        _ensure_simulator_source(session, organization.id, plant.id)
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
        _synchronize_equipment(session, organization.id)
        print(
            f"Seeded {hours:g} hours of simulated history for organization {organization.id}; user {engineer.email}"
        )


def _ensure_sensors(session: Session, organization_id: UUID) -> None:
    """Add demo sensors introduced after the original seed, if missing."""
    existing_tags = set(
        session.scalars(select(Sensor.tag).where(Sensor.organization_id == organization_id))
    )
    created = False
    for tag, name, unit, kind, minimum, maximum in SENSOR_DEFINITIONS:
        if tag in existing_tags:
            continue
        equipment = session.scalar(
            select(Equipment).where(Equipment.organization_id == organization_id)
        )
        if equipment is None:
            return
        session.add(
            Sensor(
                organization_id=organization_id,
                equipment_id=equipment.id,
                name=name,
                tag=tag,
                unit=unit,
                measurement_type=kind,
                sampling_interval_s=300,
                min_valid_value=minimum,
                max_valid_value=maximum,
            )
        )
        created = True
    if created:
        session.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=168.0)
    arguments = parser.parse_args()
    seed(arguments.hours)
