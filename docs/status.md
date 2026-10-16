# Status and limitations

Reviewed source and tests are included. Private operational records are excluded.

## Implemented

- Capsule tenant inventory and stable Grafana identity-based portal authorization.
- Tenant policy, up to three namespaces and one verified KAI queue.
- Bounded DRA GPU smoke requests, idempotent execution journal and conditional cancellation.
- Observed device allocation and waiting-job counts with explicit unknown/error states.
- A local synthetic quota simulator and authenticated demo UI.
- Persistent, guarded quota changes for two specifically bound Kueue queues kept on Hold.

## Not implemented or not established by this release

- General tenant creation with multi-resource shared ranges and a cluster-wide sharing ledger.
- Automatic borrowing/reclaim or forced workload eviction.
- GPU memory isolation or VRAM enforcement.
- A general native Device Plugin/MPS workload adapter in this console.
- Performance guarantees, scheduler score improvements, or workload throughput benefits.
- Production-wide identity, admission, GPU, CNI and recovery validation for a new installation.

The broader policy design distinguishes a default budget, a maximum boundary, shared excess, current admission budget and measured usage. Those design concepts are not proof that all associated controllers exist.

Source-file fingerprints and verified revisions are listed in [provenance](provenance.md).
