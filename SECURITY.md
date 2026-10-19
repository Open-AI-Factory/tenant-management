# Security

This is an experimental integration, not a production security certification. The default generated configuration disables tenant API mutations, policy reconciliation and Kueue control. Observation of existing executions and updates to their journal ConfigMap continue, so the runtime is not fully read-only. Its RBAC and cluster-scoped admission objects still require review before installation.

## Report an issue

Do not post credentials, cluster data or exploit details in a public issue. Use this repository's private vulnerability reporting feature when available. If it is unavailable, open a minimal issue requesting a private contact channel without sensitive details.

## Boundaries

- Identity is verified through Grafana and stable provider/subject identifiers. Browser-supplied role headers are not authority.
- Capsule tenant UID, namespace ownership, policy generation and queue/template identities must agree.
- Portal membership is separate from Kubernetes/Capsule owner permissions.
- GPU cancellation uses owned object identity. A delete request alone is not proof of driver release.
- Forecasts, requested capacity, admission budget, allocated devices and measured memory are separate quantities.
- DRA whole-GPU/MIG requests do not imply MPS support or VRAM isolation.
- The local simulator uses synthetic data and binds to loopback. Do not expose it as a multi-user service.

Keep secrets and deployment output outside Git. Do not disable TLS verification to make an environment work. The public-content check catches selected leak patterns; it does not replace human review or a dedicated secret scanner.
