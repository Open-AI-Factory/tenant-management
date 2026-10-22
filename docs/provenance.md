# Source provenance

The source snapshot is based on revision `e30d011beb2d0846d989871bb484dcddd2c98c96`. An explicit allowlist includes 78 source, test and UI files. Private operational documents, credentials, live manifests, logs and real tenant records are excluded. Environment-specific endpoints and example target names use portable configuration; deployment defaults retain disabled mutation gates.

[source-provenance.json](source-provenance.json) records public SHA-256 fingerprints and verified source revisions after LF line-ending normalization. Of the 78 files, 41 match verified source revisions and 37 contain working-tree content or publication-specific changes. Source revision IDs are references to the source inputs, not links to a public upstream repository.

The manifest covers the product files. Documentation, packaging metadata, CI and the content checker are separate publication assets. Original product files remain unchanged. See [validation](validation.md) for the tested scope and [status](status.md) for implementation limits.
