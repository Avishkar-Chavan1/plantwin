# External-action roadmap

These activities cannot be truthfully completed by repository code alone. They are required inputs or independent evidence for a production or plant-facing decision; do not mark them complete merely because a document or test fixture exists.

## Security and compliance

- [ ] Engage an authorized independent penetration test against the deployed API, web application, identity boundary, and cloud/on-prem configuration. Track scope, findings, remediation, and retest evidence.
- [ ] Select the organization’s control baseline and have accountable owners assess it. Any SOC 2, ISO 27001, IEC 62443, privacy, export-control, or sector-specific claim needs its own evidence and authorization.
- [ ] Obtain a production identity-provider tenant, OIDC application registration, approved redirect URIs, JWKS/issuer details, role/group mapping, session policy, and key-rotation process.
- [ ] Define and test organization-approved secrets management, certificate issuance/renewal, vulnerability response, patch window, and incident-notification procedures.

## Plant and process evidence

- [ ] Obtain written authorization and data-governance approval for historian, MQTT, or OPC UA read-only access. Record tags, units, data ownership, retention, and acceptable-use limits.
- [ ] Have process/control engineers verify tag mapping, instrument calibration/quality, timing/dead-time assumptions, operating envelopes, model parameters, and acceptance criteria.
- [ ] Conduct HAZOP, management-of-change (MoC), cybersecurity review, and operations review before any pilot. ProcessTwin must remain advisory-only; no review authorizes automatic or direct control.
- [ ] Run a monitored, customer-approved pilot using independent chronological holdouts and a documented human-review workflow. Public/synthetic benchmarks do not replace this.

## Operations and recovery

- [ ] Provision a supported PostgreSQL/Timescale environment and object storage; execute a backup and restore exercise with measured recovery evidence.
- [ ] Supply a Docker/Kubernetes build environment with approved registry access, image signing/provenance policy, network controls, TLS termination, monitoring, and alert delivery.
- [ ] Agree service objectives, retention period, escalation owners, on-call coverage, air-gapped update path if relevant, and incident/rollback authority.
- [ ] Perform a representative load test in an approved non-production environment; publish the exact workload, hardware/runtime, results, and limits.
