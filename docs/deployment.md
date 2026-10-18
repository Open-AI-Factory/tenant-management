# Deployment review

The public package is an integration prototype. The simulator is immediately runnable; the cluster console requires adaptation and an isolated validation environment.

## API contracts

The code calls Capsule `capsule.clastix.io/v1beta2`, KAI Queue `scheduling.run.ai/v2`, DRA `resource.k8s.io/v1`, and Kubernetes validating admission policy APIs. The optional budget path calls Kueue `kueue.x-k8s.io/v1beta2`. Verify these schemas and the target driver behavior in your own cluster.

## Generate, inspect, then decide

1. Configure the origins, identity-provider key and existing TLS Secret as described in [configuration](configuration.md).
2. Run `python -B build.py` locally.
3. Inspect every object in the ignored `resources.json`. It includes cluster-scoped RBAC and admission policies even while feature gates are closed.
4. Validate namespace/ServiceAccount contracts, existing object ownership, DNS peers, TLS chain, Grafana session scope and single-writer behavior.
5. Run server-side dry runs against an explicit isolated kubeconfig before considering an apply. Back up existing objects and use UID/resourceVersion preconditions for changes.

This repository does not automate a production rollout. `prepare_rollout.py` prepares conditional changes from supplied identity records; it does not grant authorization to overwrite existing objects. `collect_tenant_baseline.py` and `verify_tenant_mvp.py` are operator helpers, not ordinary tests. Their output can contain cluster information and must stay out of Git.

## GPU path

The console's smoke-test adapter uses DRA and a pinned public PyTorch image. It expects one selected device and compatible device classes/attributes. Inventory currently recognizes whole GPUs and the specified MIG profile; this is not a generic Device Plugin or MPS adapter. Verify device selection, actual CUDA visibility and driver return independently.

## Kueue prototype

The exported example names are `example-tenant-a/b`, `example-tenant-queue-a/b`, `example-gpu-flavor`, `example-kai-queue` and `example-gpu-pool`. They are exact contract names, not a wildcard permission grant.

The controller supports two targets and a two-device envelope. It requires held queues, execution locks and verified bindings. Keep `stopPolicy: Hold`; changes to nominal quota in this path do not demonstrate workload admission, execution speedup or resource sharing. Generalizing these constraints is a separate feature.
