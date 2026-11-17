# Historical deployment examples

These files are retained for reference, not supported deployment entry points. They contain outdated Python,
environment-variable, health-check, routing, and service assumptions. Do not deploy them or reuse their sample
credentials. Relative paths in the archived Compose file refer to its former location at the repository root.

Use the root [Dockerfile](../../Dockerfile) and [Compose configuration](../../docker-compose.yml), or the
[Kubernetes Kustomization](../k8s/kustomization.yaml). The one image selects the portal or authentication factory
with `PORTAL_APP`; no separate service-specific Dockerfile is needed.

The optional mail HTTP API is not the portal's configured delivery transport and has no durable automatic worker.
The authentication service uses the account-role implementation, not the experimental RBAC example.
