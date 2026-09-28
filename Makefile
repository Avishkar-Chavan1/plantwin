.DEFAULT_GOAL := help

PYTHON ?= python
API := $(PYTHON) -m uvicorn apps.api.processtwin_api.main:app --host 0.0.0.0 --port 8000 --reload

.PHONY: help install api demo seed simulate train evaluate migrate migration downgrade test lint typecheck check docker-up docker-down

help:
	@echo "Targets: install, api, demo, seed, simulate, train, evaluate, migrate, test, lint, typecheck, check"

install:
	$(PYTHON) -m pip install -e ".[dev]"

api:
	$(API)

# Starts infrastructure, applies migrations, seeds seven days of simulated data,
# registers a validation model, then starts the dashboard/API/simulator.
demo:
	docker compose up --build

seed:
	$(PYTHON) -m apps.simulator.processtwin_simulator.seed_demo --hours 168

simulate:
	$(PYTHON) -m apps.simulator.processtwin_simulator.run --steps 240

train:
	$(PYTHON) -m apps.worker.processtwin_worker.train

evaluate:
	$(PYTHON) -m apps.worker.processtwin_worker.evaluate

migrate:
	alembic upgrade head

migration:
	alembic revision --autogenerate -m "$(MESSAGE)"

downgrade:
	alembic downgrade -1

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check apps packages connectors tests

typecheck:
	$(PYTHON) -m mypy apps packages connectors

check: lint typecheck test

docker-up:
	docker compose up --build

docker-down:
	docker compose down
