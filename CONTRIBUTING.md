# Contributing

Keep changes scoped to a behavior, its tests and the relevant documentation. Preserve identity, ownership, resource-version, admission and return-verification checks. Do not turn unknown observations into zero usage or successful return.

Use Python 3.9-compatible syntax and standard-library runtime dependencies. The existing UI is plain HTML/CSS/JavaScript. Do not add a framework or a new cluster integration as part of an unrelated fix.

Run the commands in [testing](docs/testing.md) before submitting a pull request. Report exactly what was exercised: unit tests, local HTTP, browser fixtures and live-cluster checks are different evidence. Never include credentials, real tenant data, private addresses, live manifests or test logs containing operator information.

## Commit messages

Use English [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
feat(tenants): validate namespace ownership before queue binding
fix(quota): reject stale forecasts after policy changes
test(auth): cover cross-tenant membership access
docs(deploy): explain disabled mutation gates
```

Describe the actual change and include relevant validation.

## Live testing

No contribution requires access to the original development cluster. If live testing is necessary, use an isolated environment, explicit kubeconfig, owned resources and recorded rollback conditions. Do not run live helper scripts as ordinary unit tests.
