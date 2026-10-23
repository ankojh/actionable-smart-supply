# Retired data

`pre_northstar_happy_path/` contains the former sample-data family, backend source
and evaluator fixtures, Snowflake load JSON files, historical acceptance outputs,
and historical research scenario data. The manifest records **184 original files**,
their original paths, archive paths, byte counts, and SHA-256 hashes.

These files are preserved unchanged. Do not use this directory as active agent
evidence, current application data, or an input to a Snowflake loader. The new
local pack is [`data/northstar_happy_path`](../data/northstar_happy_path).

Restore only by an explicit future choice: copy the selected file from its
`archive_path` to `original_path` after checking that the destination has no newer
data, and verify its hash. The current generator never reads this archive.
The archive manifest is used only by a preservation test.

No remote Snowflake data was deleted or modified. Old cloud snapshots remain
historical; selecting or migrating cloud data is outside this data-generation task.

The pull of `ff061e0` added two acceptance files and updated two archived files.
Their two earlier versions are preserved in `previous_versions/93f0cdb/` and
listed in the manifest separately. Latest archived versions match the pulled branch.
