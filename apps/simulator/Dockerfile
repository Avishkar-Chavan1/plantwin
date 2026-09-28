FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY apps ./apps
COPY packages ./packages
COPY connectors ./connectors
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .
CMD ["python", "-m", "apps.simulator.processtwin_simulator.run", "--forever"]
