# Testing

## Python and package tests

```sh
python -B -X utf8 -m unittest discover -v
```

No pip packages are required. Tests use fake Kubernetes clients and loopback HTTP servers. Package tests execute `build.py`, regenerate ignored `resources.json`, extract its ConfigMap and check runtime imports and local HTTP behavior.

## JavaScript

```sh
node --check app.js
node --check quota_simulator.js
node --check kueue_quota.js
```

## Optional browser tests

Install Node.js 22.13 or newer, pnpm 11.25.0, Google Chrome and the pinned development dependency:

```sh
pnpm install --frozen-lockfile --ignore-scripts
pnpm run test:browser
```

The tests launch local Python servers, use synthetic login fixtures and Chrome through Playwright. `python` must resolve to Python 3.9 or newer. Test-only cookie values are not production credentials. Screenshots and logs go in ignored `work/`.

The five browser suites cover the quota simulator, quota scenario controls, tenant views, usage views and held Kueue budget UI. They do not prove live authentication, GPU allocation, CNI isolation or production authorization.

## Public-content check

```sh
python -B scripts/check_public_content.py
```

The check rejects selected token/key patterns, private network addresses, personal workstation paths and prohibited artifact types. It prints locations and finding categories, not matching secret values. Generated manifests and dependencies are not publication inputs. Review the actual staged files and commit history before pushing.

## Evidence

Record the command, environment and checked revision. Do not reuse historical live-test counts as evidence for a new public snapshot. See [release validation](validation.md) for this export's checks.
