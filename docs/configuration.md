# Configuration

`build.py` reads environment configuration when generating `resources.json`. No configuration command applies resources to a cluster.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUBLIC_ORIGIN` | `https://tenant-admin.example.invalid` | Exact browser origin for the console |
| `GRAFANA_URL` | `https://grafana.example.invalid` | Grafana origin and rendered navigation links |
| `GRAFANA_INSTANCE_ID` | `tenant-admin-grafana` | Stable identity-provider instance key |
| `TLS_SECRET_NAME` | `tenant-admin-tls` | Existing TLS Secret mounted at `/tls` |
| `TENANT_NODE_NAME` | unset | Optional node placement; no node is pinned by default |

Origins must be valid HTTPS origins, without credentials, paths, queries or fragments. Reserved `example.invalid` addresses deliberately do not work as deployment destinations.

Example PowerShell configuration:

```powershell
$env:PUBLIC_ORIGIN = 'https://console.example.org'
$env:GRAFANA_URL = 'https://grafana.example.org'
$env:GRAFANA_INSTANCE_ID = 'example-grafana'
$env:TLS_SECRET_NAME = 'tenant-admin-tls'
python -B build.py
```

Use actual operator-owned endpoints for an installation. Review how Grafana session cookies reach the console; different hostnames do not automatically share cookies. The runtime currently uses the mounted certificate file as Grafana trust material as well. Supply the correct trust chain and verify hostname checks rather than disabling TLS verification.

The namespace remains `monitoring`, the console ServiceAccount is `tenant-admin`, and the example Service is NodePort `30443`. Several admission and actor contracts refer to these names; changing only one manifest is not a supported namespace migration.

The generated gates `TENANT_MUTATIONS_ENABLED`, `TENANT_RECONCILE_ENABLED` and `KUEUE_QUOTA_ENABLED` remain `false`. DNS peer configuration starts empty. Enabling gates is a separate operator action after deployment review and live validation.

These gates block tenant API mutations, policy reconciliation and Kueue budget control. Observation of existing executions and updates to their journal ConfigMap continue even with all three gates disabled. This is not a fully read-only runtime.
