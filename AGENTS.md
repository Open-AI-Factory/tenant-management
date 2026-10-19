# Contributor guidance

- Read README.md and docs/status.md before changing behavior.
- Preserve Python 3.9 compatibility and the standard-library runtime.
- Keep identity/UID/generation/resourceVersion checks, fail-closed admission and disabled deployment gates.
- Do not generalize the held two-target Kueue prototype without an explicit design and tests.
- Do not commit generated resources.json, operator records, real cluster data, credentials or personal paths.
- Test offline by default. No live cluster operations are implied by tests or documentation changes.
- Use English Conventional Commits. Describe implemented and validated behavior separately.
- Update relevant public documentation when behavior or operating requirements change.
