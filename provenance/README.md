# Evidence provenance

`evidence_index.csv` indexes current public evidence by repository-relative paths
and SHA-256 hashes of the distributed LF bytes.
`rewiring_audit_inputs.json` records the source stages and transformations of
the compact metadata and per-replicate audit snapshots. The dedicated replay
protocols under `results/core_evidence/` record calculation settings and hashes.

Protocols contain the parameters and identities needed to reproduce the saved
analyses. Input names are repository-relative; regenerated raw intermediates use
the logical prefix `reproduced/raw/`, resolved through the configured output
root. Custom external inputs are identified by their filename and hash rather
than the machine's directory. The release manifest records current file bytes.

Document-layout checks, bibliography checks, old screening plans and redundant
copies of generated replay outputs are not part of the distributed artifact.
