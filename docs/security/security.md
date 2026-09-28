# Security architecture

- Passwords are bcrypt hashes; raw credentials are never logged.
- JWT secrets and external service endpoints come from typed settings/environment variables.
- The API resolves user memberships server-side and constrains SQL queries by organization.
- Roles are `OWNER`, `ADMIN`, `ENGINEER`, `OPERATOR`, `VIEWER`; mutating engineering endpoints require an authorized role.
- CSV ingestion validates extension, byte length, required fields, timestamps and values before persistence.
- CORS origins are explicit and HTTP responses receive conservative security headers.
- Audit logs are created through an append-only application service. There is no normal API to edit or remove them.

Production users should terminate TLS before the API, use an OIDC provider or rotate signing secrets, enable a managed backup policy, restrict database networking and apply a rate-limit store shared across API instances.

