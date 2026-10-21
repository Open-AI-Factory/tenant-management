# Public snapshot validation

Checked on 2026-10-22 on Windows with Python 3.9.5, Node.js 24.19.0, pnpm 11.25.0, Playwright 1.64.1 and installed Google Chrome.

| Check | Result | Boundary |
| --- | --- | --- |
| `python -B -X utf8 -m unittest discover -v` | 208 tests passed | Fake Kubernetes clients, loopback HTTP, package generation and public configuration |
| `pnpm run test:syntax` | Passed | Syntax of the three application JavaScript files |
| `pnpm run test:browser` | Five suites passed, 57 named checks | Synthetic quota, tenant and usage views; held Kueue UI; desktop and mobile fixtures |
| `python -B scripts/check_public_content.py` | No findings in publication inputs | Selected exposure patterns and prohibited artifacts, plus manual review |
| Source preservation | 78 source files retained their original hashes | The original product working tree was not edited for this export |

The browser suites reported no page/browser errors. Python package tests regenerated the ignored manifest and verified imports from its packaged ConfigMap. Public configuration tests covered matching HTML/JavaScript/Deployment origins and rejected malformed origin values.

These checks apply to the final assembled public snapshot, not to every intermediate import commit. No live cluster deployment, GPU workload, Grafana login, Capsule isolation or Kueue admission test was run for this publication. Historical private experiments are not presented as validation of a new installation. GitHub CI separately checks Python 3.9 and 3.12 after publication; local checks do not establish its result in advance.

The source-file hashes are in [source-provenance.json](source-provenance.json). Test logs and screenshots remain outside the publication inputs. Reproduce the checks with [testing.md](testing.md).
