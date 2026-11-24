# Evidence provenance

`evidence_index.csv` and `figure_table_sources.json` index current public evidence
by repository-relative paths and SHA-256 hashes of the distributed LF bytes.
`rewiring_audit_inputs.json` records the source stages and transformations of
the compact metadata and per-replicate audit snapshots. The dedicated replay
protocols under `results/core_evidence/` record calculation settings and hashes.

`historical/` preserves the original evidence index, source map, bibliography
check and manuscript layout audit as dated historical records. Their local
paths, old evidence locations and document statistics describe the original
workspace; they are not commands, required inputs, or verification of the
current release. They must not be interpreted as coverage of later references,
later document layouts, or the revised statistical replay.
