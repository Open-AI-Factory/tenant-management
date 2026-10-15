# Tenant Management

An experimental tenant console for Capsule and KAI, with a local quota simulator and a constrained Kueue budget-control prototype.

The console groups namespaces under a verified Capsule tenant, connects a tenant policy to a KAI queue, and records bounded GPU smoke executions. The simulator runs without Kubernetes. The Kueue prototype changes quotas for two explicitly bound, **held** example queues; it does not admit GPU workloads.

## Try the simulator

Requires Python 3.9 or newer. Python runtime code uses only the standard library.

```sh
git clone https://github.com/Open-AI-Factory/tenant-management.git
cd tenant-management
python -B -X utf8 quota_simulator.py --port 8894
```

Open <http://127.0.0.1:8894>. The interface is currently in Korean. Use the next-step control to inspect forecasts, quota decisions and held changes. The built-in scenario is synthetic, uses a simulated clock, and never contacts Kubernetes.

To replay the scenario from another terminal:

```sh
python -B -X utf8 dummy_profiler.py --url http://127.0.0.1:8894
```

## What is included

| Area | Scope |
| --- | --- |
| Tenant console | Capsule inventory, stable Grafana identity, portal membership and policy state |
| Namespace/KAI binding | Up to three namespaces sharing one tenant queue; UID and policy-generation checks |
| GPU smoke execution | Fixed image, one Pod/container/device, DRA request, 30–180 second hold, bounded deadline |
| Execution records | Idempotent request IDs, durable journal, conditional cancellation and explicit return states |
| Usage view | DRA allocation and waiting-job observations; missing observations remain unknown |
| Local quota simulator | Synthetic forecasts, bounds, freshness, cooldown and downward-hold behavior |
| Kueue prototype | Two example targets, a two-device envelope, persistent state and `stopPolicy: Hold` |

The broader shared-resource design is not implemented. This release does **not** provide general resource borrowing/reclaim, VRAM isolation, performance guarantees, a native Device Plugin adapter for the console, or an arbitrary-workload submission service. A resource budget is not measured GPU memory usage. See [status and limitations](docs/status.md).

## Validate locally

```sh
python -B -X utf8 -m unittest discover -v
node --check app.js
node --check quota_simulator.js
node --check kueue_quota.js
python -B scripts/check_public_content.py
```

Tests regenerate an ignored `resources.json` file. They use fake Kubernetes clients and local HTTP servers, not a live cluster. Optional browser tests are described in [testing](docs/testing.md).

## Cluster integration

The full console is an in-cluster application. It expects compatible Capsule, KAI and DRA APIs, Grafana session authentication, TLS material and operator-reviewed RBAC. Kueue is optional for the separate held-queue prototype. `build.py` creates **reviewable example manifests**, not a turnkey production installation.

```sh
python -B build.py
```

The generated defaults use reserved `example.invalid` domains, no fixed node placement, and disabled mutation/reconcile/Kueue gates. Configure your environment and review [deployment requirements](docs/deployment.md) before applying anything.

## Code map

- `server.py`, `runtime.py`, `http_app.py`: Kubernetes client, controller loop and authenticated HTTP service.
- `tenant_api.py`, `access.py`, `context.py`: tenant operations, stable identity and authorization.
- `namespaces.py`, `network_policy.py`, `admission.py`: namespace ownership and guarded policy resources.
- `executions.py`, `workloads.py`, `tenant_usage.py`: bounded GPU tests, execution journal and observations.
- `forecast_quota.py`, `quota_simulator.py`, `dummy_profiler.py`: standalone synthetic quota evaluation.
- `tenant_budget_policy.py`, `kueue_*.py`: explicitly bound Kueue budget prototype.
- `test_*.py`, `test_*ui.cjs`: unit, local HTTP and browser regression tests.

Read [architecture](docs/architecture.md), [configuration](docs/configuration.md) and [security](SECURITY.md) for the operating boundaries. Contributions follow [Conventional Commits](CONTRIBUTING.md).

Source-file fingerprints and verified revisions are documented in [provenance](docs/provenance.md).

## License

Apache-2.0. See [LICENSE](LICENSE). Third-party services and runtime images retain their own licenses; they are not bundled in this repository.
