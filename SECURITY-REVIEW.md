# Security and Code Review Findings

**Review date:** 2026-11-18
**Scope:** Repository-wide security review and code review. Findings below are reported, not remediated.

## Security findings

| # | Severity | File | Lines | Finding | Confidence |
|---|---|---|---|---|---|
| 1 | 🟠 HIGH | [src/utsu_core/programs/agm/verify.py](src/utsu_core/programs/agm/verify.py) | 84–90, 97–111 | When AGM verification mode is `auto`, submitting any decodable face and ID images grants access without identity or liveness verification. This bypasses the ID-verification gate for online registrants. | 9/10 |
| 2 | 🟡 MEDIUM | [deploy/k8s/portal.yaml](deploy/k8s/portal.yaml), [deploy/k8s/auth.yaml](deploy/k8s/auth.yaml), [deploy/k8s/maintenance-cronjob.yaml](deploy/k8s/maintenance-cronjob.yaml) | 16, 16, 22 | The manifests use the mutable `:latest` image tag. A modified image published under that tag can run with mounted production secrets. The manifests comment that production deployments should pin by digest, but do not enforce it. | 9/10 |

### Recommendations

1. Do not grant the AGM verification approval automatically without a real identity/liveness check; require reviewer approval otherwise.
2. Pin all Kubernetes workloads to the same immutable image digest and verify image signatures or attestations during deployment.

## Code review findings

| # | Severity | File | Lines | Finding | Confidence |
|---|---|---|---|---|---|
| 1 | HIGH | [src/utsu_core/programs/voting/plugin.py](src/utsu_core/programs/voting/plugin.py) | 286–315, 417–443 | Ballot tallying snapshots votes before storing the immutable result record, while an in-flight cast can write its vote, check the still-unrecorded ballot, and return success. If those operations interleave, the published record can omit an acknowledged vote. The closing grace period does not synchronize in-flight casts. | High |
| 2 | MEDIUM | [src/utsu_core/static/index.html](src/utsu_core/static/index.html), [web/src/lib/views.js](web/src/lib/views.js), [Dockerfile](Dockerfile) | 3; 7; 14 | The source registers the admin content editor, but the committed static entrypoint still loads an older bundle. Since the Docker image copies the committed static assets without building the web client, deployments can omit the editor. | High |

### Recommendations

1. Synchronize vote admission with ballot finalization and freeze the record only after all admitted casts have completed.
2. Rebuild and commit the static client bundle after frontend changes, or build it as part of the image pipeline.
