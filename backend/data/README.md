# Local scenario retired

The former `initial_case.json` is preserved under
`archive_data/pre_northstar_happy_path/backend/data/` at the repository root.
The active data pack is `data/northstar_happy_path/`.

The existing backend requires a new data-contract adapter before it can run the
new scenario. Do not restore the old file or read the archive as an automatic
fallback. This task generated data only; app integration is deferred.
