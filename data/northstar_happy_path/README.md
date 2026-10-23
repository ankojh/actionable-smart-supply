# NorthStar happy-path data pack

This is the active **local data pack**, based on the updated Notion happy case,
the supplied NorthStar ZIP, and the replacement `DOC-SUP-001` email. Everything
is fictional. No Snowflake connection, data upload, email, purchase, or stock
movement occurs when generating or validating this pack.

The story ends with **Option C: transfer 80 pilot-line drives and buy 40 from
qualified Supplier C**, after the manager challenges the initial Apex recommendation.
All records belong to decision case `NS-2026-1042`; V2 revises the same case.

## Start here

```sh
python3 -m data.northstar_happy_path.generate
python3 -m data.northstar_happy_path.validate
python3 -m unittest discover -s data/northstar_happy_path/tests -v
```

Only Python's standard library is required. Run from the repository root.
Generation is deterministic. To experiment without changing the checked-in export:

```sh
python3 -m data.northstar_happy_path.generate --output /tmp/northstar-preview
python3 -m data.northstar_happy_path.validate --output /tmp/northstar-preview
```

Use **`generated/stages/00_detection/dataset.json`** to start the role play, or
**`generated/stages/01_impact/dataset.json`** for the October 26 operational snapshot.
Advance stages explicitly using [ROLE_PLAY.md](ROLE_PLAY.md). Each stage is a
complete replacement snapshot, not additive supply. Never combine snapshots.

| Location | Purpose |
|---|---|
| `generated/stages/*/dataset.json` | Cumulative, stage-scoped agent inputs |
| `generated/stages/*/documents/` | Exact documents visible at that stage |
| `generated/tables/*.csv` and `*.ndjson` | 25 flat tables for later migration; full fixture history |
| `generated/catalog.json` | Full authoring catalog; contains later approvals and simulated events |
| `generated/data_dictionary.json` | Column types, nullability, keys, joins, future Snowflake types |
| `generated/manifest.json` | Row counts, stage order, source and output hashes |
| `context/policies.json` | Active business rules, V1/V2 objectives, time and approval semantics |
| `tests/` | Independent expected answers and regression checks; exclude from retrieval |
| `source_pack/` | Unmodified supplied inputs, including superseded narratives; provenance only |
| [SOURCE_DECISIONS.md](SOURCE_DECISIONS.md) | Reconciliation of source differences and authored assumptions |
| [COVERAGE.md](COVERAGE.md) | Requirements, evidence and negative controls |

The dataset contains 14 components, 12 purchase orders, 2 production plans,
3 contracted customers plus a forecast bucket, 2 recovery lots, 10 role-play
actors, supplier correspondence, qualification records, approvals, drafts and a
held-back simulated execution feed. Record counts are in the manifest.

## Business results

| Scenario | Robots built in horizon | Contracted on time | Gross exposure | Stated immediate expenditure | Unbuilt forecast |
|---|---:|---:|---:|---:|---:|
| Baseline | 60 | 60 / 120 | $1,080,000 | — | 130 |
| A: Apex 400 | 250 | 120 / 120 | $0 | $194,400 | 0 |
| B: pilot 80, conditional | 100 | 100 / 120 | $360,000 | Unknown at V1 | 130 |
| C: pilot 80 + Supplier C 40 | 120 | 120 / 120 | $0 | $24,000 | 130 |

B's schedule is conditional on release approval, not permission to move stock.
C consists of $22,000 purchase expenditure plus a $2,000 assumed handling cost.
It requires $170,400 less upfront commitment than A; this is **not net economic
savings**, because A also supplies forecast production and additional inventory.
The full-plan baseline shortage is 380 drives; C resolves 120, leaving **260
drives for 130 forecast robots** uncovered within the horizon.

All three options leave Pacific's original 400-unit PO open at $164,800, due
November 10. A ends the horizon with 20 drives; C ends with zero, both below
the 100-drive safety-stock warning level. Neither automatically cancels Pacific.

The latest safe material date is November 3 for SwiftLog and November 4 for
BayShip/NovaFab. Hybrid pilot stock is released October 27 and usable October 28;
Supplier C is usable October 29. Expected shipments are October 28, October 30,
and November 2 respectively. One shared line starts at most 60 robots per
business day, with two business days from material/start to shipment.

## Migration contract (later)

- UTF-8 CSV, headers, LF records, standard CSV quoting. Null is the literal `\N`,
  distinct from zero and empty text. Multiline document content is quoted.
- NDJSON has one flat object per line and native JSON null/boolean/integer values.
- Dates are `YYYY-MM-DD`. No invented intraday timestamps; date-level reservation
  deadlines expire at end of day in `America/Los_Angeles`.
- Money is integer USD cents; future `NUMBER(38,0)`. Quantities are whole `each`.
- Every table has `scenario_id`; include it in every key and join. The dictionary
  specifies composite keys, references, and Snowflake-compatible target types.
- Do not infer types from CSV or use a whole-catalog retrieval index. Preserve
  `available_stage` filtering and keep future confirmations, decisions and outcomes
  unavailable to earlier stages. Load one representation (CSV or NDJSON), not both.
- Source documents and normalized offers describe the same facts. They do not
  create extra inventory. Neither proposals nor approvals are receipt events.
- Load masters first, then operational tables/actors/documents, then evidence
  links/offers/qualifications, then messages/events/approvals and drafts. Use
  explicit idempotent keys and validate locally; do not assume the database will
  enforce foreign keys. No migration SQL or cloud loader is executed here.

## Repository status

Older data and acceptance results are in
[`archive_data/pre_northstar_happy_path`](../../archive_data/pre_northstar_happy_path).
They are not part of this dataset and must not be loaded or retrieved as current
evidence. The archive manifest records original paths and hashes.

The default application now reads stages 00–04 from the dedicated Snowflake
NorthStar schema, with deterministic recovery calculations, live hosted Claude
specialists and Company Brain, cloud reviewer decisions and unsent drafts.
The typed source tables and five planning snapshots have been ingested. See the
[application integration guide](../../docs/northstar-integration.md).

The retired workflow and its loaders still use the previous contract. The
NorthStar loader is `snowflake/load/northstar.py`; verification is
`snowflake/verify/northstar.py`. No runtime fallback to archived data is provided.
Existing legacy Snowflake tables are preserved.
