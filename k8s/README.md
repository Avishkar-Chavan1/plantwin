# Kubernetes deployment (base)

`kubectl apply -k k8s/base` applies:

| Manifest | Contents |
| --- | --- |
| `base/namespace.yaml` | `processtwin` namespace |
| `base/configmap.yaml` | Non-sensitive configuration (key names match `Settings`) |
| `base/api-deployment.yaml` | API Deployment (migration init container) + Service |
| `base/web-deployment.yaml` | Next.js standalone Deployment + Service |

Stateful dependencies (PostgreSQL/TimescaleDB, Redis, MinIO, MLflow) are **not** part of
this base: run them as managed services or as a separately operated stack, then point the
API at them.

## 1. Create the secrets first

```bash
kubectl create namespace processtwin

kubectl -n processtwin create secret generic processtwin-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://processtwin_app:pass@host:5432/processtwin' \
  --from-literal=DATABASE_ADMIN_URL='postgresql+psycopg://owner:pass@host:5432/processtwin' \
  --from-literal=JWT_SECRET="$(openssl rand -base64 48)" \
  --from-literal=METRICS_TOKEN="$(openssl rand -base64 48)"
```

Key names are documented in `base/secret.example.yaml`. If the Secret is missing the pod
fails with an explicit configuration error — it never starts with an insecure default.

### Database roles (row-level security)

Postgres table owners bypass row-level security, so the two URLs must be different roles:

- `DATABASE_ADMIN_URL` — table owner; used **only** by the migration init container.
- `DATABASE_URL` — non-owner application role; the API connects with it so the
  `processtwin_*` tenant policies are actually enforced.

Provision the app role before the first migration/deployment. Its default
privileges cover tables created by the owner migration role; re-run the script
after a restore or when grants change:

```bash
psql "$DATABASE_ADMIN_URL" -v app_role=processtwin_app \
  -v app_password='replace-with-a-secret' -f scripts/postgres_app_role.sql
```

If both keys point at the same owner role the platform still works, but database-level
tenant isolation silently stays dormant — only the application-layer filters apply.

## 2. Set your domain

Edit `base/configmap.yaml`:

- `CORS_ORIGINS` — absolute HTTPS origins, comma separated

Production settings validation refuses to start on placeholder, `http://` or `*` origins.

Set `NEXT_PUBLIC_API_URL` separately when building the web image. Next.js embeds this
public (non-secret) value into browser JavaScript during the build, so a Kubernetes
pod environment variable cannot change it after the image has been created.

## 3. Push images and apply

```bash
NEXT_PUBLIC_API_URL=https://api.your-domain.com \
  docker compose -f docker-compose.prod.yml build
docker tag processtwin/api:0.1.0   registry.example.com/processtwin/api:0.1.0
docker tag processtwin/web:0.1.0   registry.example.com/processtwin/web:0.1.0
docker push registry.example.com/processtwin/api:0.1.0
docker push registry.example.com/processtwin/web:0.1.0

cd k8s/base
kustomize edit set image processtwin/api=registry.example.com/processtwin/api:0.1.0
kustomize edit set image processtwin/web=registry.example.com/processtwin/web:0.1.0
cd ../..

kubectl apply -k k8s/base
kubectl -n processtwin rollout status deploy/processtwin-api
```

## 4. Verify

```bash
kubectl -n processtwin get pods
kubectl -n processtwin port-forward svc/processtwin-api 8000:8000
curl -fsS http://localhost:8000/live
```

## Security posture of the manifests

- Containers run as non-root with a read-only root filesystem, `capabilities.drop: [ALL]`,
  `allowPrivilegeEscalation: false` and `RuntimeDefault` seccomp.
- `automountServiceAccountToken: false` — the workloads need no API access.
- Writable paths are explicit `emptyDir` mounts (`/tmp`, `/app/logs`, `/app/tmp`,
  `/.next/cache`).
- `maxUnavailable: 0` rolling updates plus readiness probes give zero-downtime deploys.
- The API process never mutates schema. Each pod has a migration init container;
  PostgreSQL advisory locking serializes concurrent rollouts or scale-outs.

## Not included (tracked in `docs/ROADMAP_EXTERNAL.md`)

Ingress/TLS termination (add your ingress controller and certificate policy), network
policies, PodDisruptionBudgets, HPA, image signing/provenance, and a GitOps promotion flow.
