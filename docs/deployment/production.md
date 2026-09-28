# Deployment notes

For a small installation, deploy `docker-compose.prod.yml` behind an HTTPS reverse proxy, supply production secrets through a secret manager or injected environment, and schedule database/object-storage backups. Test restoration before describing recovery objectives.

For AWS, separate app containers (ECS/EKS) from RDS PostgreSQL/Timescale-compatible storage, ElastiCache Redis, S3/MinIO-compatible object storage, an ALB, CloudWatch/OpenTelemetry collection and Secrets Manager. Keep the deployment layer outside the physics and API packages so it cannot alter engineering behavior.

Neither topology authorizes direct plant control. Connectivity adapters must be separately assessed, allow-listed and configured by a customer engineer.

