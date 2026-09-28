from __future__ import annotations

from sqlalchemy import select

from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.models import ModelVersion


def evaluate() -> None:
    with SessionLocal() as session:
        models = list(
            session.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc()))
        )
        if not models:
            print("No registered models. Run make train first.")
            return
        for model in models:
            print(f"{model.name} {model.version}: held-out metrics {model.metrics.get('test')}")


if __name__ == "__main__":
    evaluate()
