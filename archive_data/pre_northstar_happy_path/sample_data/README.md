# Synthetic development data

A small, reproducible fixture pack for building the supply-chain recovery planner.
All companies, inventory, prices, orders, and messages are fictional. This is
development data, not a presentation dataset or evidence of production performance.

**Start with `generated/baseline_delay/dataset.json`.** It contains a complete
connected snapshot. The other four directories are independent alternative
snapshots, not additional supply to combine with the baseline.

## Scope

- Two plants, one warehouse, two finished products, ten components.
- Four suppliers: the original supplier plus three alternative suppliers.
- Three customers, four customer orders, and four linked production orders.
- Sixteen tables, including BOMs, revision-specific stock, inbound deliveries,
  supplier offers, transfer lanes, qualification evidence, and supply events.
- Fourteen calendar days: November 2–15, 2026. The first shortage deadline is
  November 5. Dates are fixed scenario dates, not relative to the wall clock.
- One main constrained component, MC-100 R2. Other components have ample stock
  but still participate in BOM validation. MC-100 R1 stock is deliberately unusable.

## Folder layout

```text
sample_data/
  README.md
  model.py                        # Fields, types, relationships, and descriptions
  generate.py                     # Deterministic development fixture generator
  validate.py                     # Integrity checks and small reference oracle
  generated/
    manifest.json                 # Scenario descriptions, row counts, SHA-256 hashes
    data_dictionary.json          # Every exported field explained
    schema.sql                    # Optional Snowflake SAMPLE_* table definitions
    <scenario_id>/
      dataset.json                # Complete application-ready snapshot
      tables/*.ndjson             # Same rows, one table per file, with scenario_id
      inbox.json                  # Email records for an inbox UI, with scenario_id
      documents/*.txt             # Readable emails with headers and signatures
  tests/
    expected_results.json         # Manually specified test answers; do not serve to agents
    expected_extractions.json     # Evidence-backed extraction targets for selected messages
    test_sample_data.py
```

The generator uses only Python's standard library. There is no network access,
API key, LLM call, or database write. Python 3.9+ is sufficient for this folder;
the existing backend environment also works.

## Generate and validate

Run from the repository root:

```bash
python3 -m sample_data.generate
python3 -m sample_data.validate
python3 -m unittest discover -s sample_data/tests -v
```

To use the existing environment, substitute `backend/.venv/bin/python` for
`python3`. To generate elsewhere:

```bash
python3 -m sample_data.generate --output /tmp/supply-chain-samples
```

Generation overwrites the managed files at the selected output path. Edit the
generator to change fixtures, then regenerate; do not edit the generated copies.
Output is byte-for-byte repeatable, with no timestamps or random values changing
between runs. Test expectations are maintained independently and are not rewritten.

## Email format and who says what

All source documents are now represented as simulated emails. `kind` still
distinguishes supplier updates, quotes, qualification notices, and policy notices.
Each email carries `message_id`, `thread_id`, `in_reply_to_message_id`, `from_name`,
`from_email`, `from_organization`, `to_name`, `to_email`, `cc_name`, `cc_email`, and
`subject`. `issued_at` is the UTC send time. `document_id` remains the stable key
used by supply records and citations.

| Sender | Organization / role | Address |
| --- | --- | --- |
| Alex Chen | Supplier A, account manager | `alex.chen@anchor-components.example` |
| Priya Shah | Supplier B, supply coordinator | `priya.shah@bridge-electronics.example` |
| Daniel Rivera | Supplier C, express supply | `daniel.rivera@cobalt-express.example` |
| Elena Brooks | Supplier D, legacy parts sales | `elena.brooks@delta-legacy.example` |
| Morgan Lee | Atlas Robotics, supplier quality | `morgan.lee@atlas-robotics.example` |
| Jordan Ellis | Atlas Robotics, operations policy | `jordan.ellis@atlas-robotics.example` |

Messages go to **Sam Taylor**, the buyer (`sam.taylor@atlas-robotics.example`),
and copy **Riley Park**, the plant planner (`riley.park@atlas-robotics.example`).
Qualification emails come from the internal quality team; suppliers author their
own quotes and delivery commitments. Bodies include greetings, signatures, and a
simulation label. Addresses use reserved `.example` domains; no emails are sent.

For example, open
`generated/supplier_capacity_reduced/documents/DOC-QUOTE-B-UPDATE.txt` to see
Priya's revised offer to Sam. Its `In-Reply-To` points to the original quote's
`Message-ID`, and both share a thread. `supersedes_document_id` continues to record
which business commitment is replaced. Threads and email IDs are scenario-scoped
so alternative realities cannot be accidentally combined in one inbox.

Use `inbox.json` for an inbox view and `documents/*.txt` for readable evidence.
The TXT emails include From, To, Cc, Subject, Date, Message-ID, and reply headers
where applicable. Both formats contain the same body and attribution as
`source_documents` in `dataset.json`.

This changes the dataset contract to version **1.1.0**. Regenerated `schema.sql`
includes the new email columns. If you already created version 1.0 tables, use a
fresh development schema or migrate the columns before loading; `CREATE TABLE IF
NOT EXISTS` does not alter an existing table.

## Scenarios and expected behavior

All recovery costs below are **incremental USD costs**, including fixed fees.

| Scenario ID | Change | Recovered controllers | Uncovered controllers | Recovery cost |
| --- | --- | ---: | ---: | ---: |
| `baseline_delay` | 1,500 controllers arrive Nov 9 instead of Nov 4 | 1,500 | 0 | $3,800 |
| `supplier_capacity_reduced` | Supplier B: 400 arrive Nov 4; 300 arrive Nov 8 | 1,500 | 0 | $4,700 |
| `donor_demand_increased` | Plant B production increases from 300 to 450 products | 1,500 | 0 | $5,300 |
| `insufficient_supply` | Supplier B has 400 on time; express Supplier C has 200 | 1,300 | 200 | $3,500 |
| `no_disruption` | Original shipment arrives on time | 0 | 0 | $0 |

The baseline plan transfers 300 from Plant B and 400 from Warehouse C, buys 700
from Supplier B, and buys 100 from Supplier C. Plant B has 900 controllers on
hand but needs 600 for its own November 6 production. Only 300 are spare.

In the insufficient-supply case, 200 missing controllers mean 100 finished units
of `WO-A-NORMAL` remain unfilled. All priority-1 orders and all Plant B commitments
are protected. The unfilled order value is $18,000; that is exposure, not a claim
of lost revenue or profit. Backlog is reported and not silently rescheduled.

Supplier D's R1 parts are approved **for R1**, but incompatible with production
requiring R2. Approval and compatibility are separate checks.

## Business rules and units

1. All component and product quantities are whole units (`each`). BOM quantities
   convert finished products into component demand. There is no second consumption
   table to double-count those requirements.
2. Money is integer USD cents. Fixed fees apply once per selected offer or transfer
   lane. Unit premiums exclude the ordinary purchase price. Holding costs,
   cancellation costs, transport emissions, and taxes are outside this sample.
3. Each inventory snapshot is opening physical stock. Usable stock is on-hand
   minus quality hold, tracked separately by revision. Customer/production demand
   is not also deducted as a reservation.
4. Daily ordering is receipts, transfers, then production. Same-day receipts can
   cover production. Transit uses calendar days, including weekends. Intraday
   cutoffs are not modeled. Document timestamps are UTC.
5. The source document, supply event, PO, and delivery describe the same shipment.
   Only `inbound_deliveries` contributes existing inbound quantity. Events and
   documents never create additional stock.
6. Offers represent optional new purchases. Do not add their full availability
   to inventory unless a plan selects that quantity. Transfer capacity is not
   evidence that the donor can spare the stock.
7. Qualification is supplier + component + revision + validity dates. There are
   no automatic revision substitutions.
8. Protect all donor-plant production over the entire horizon. At Plant A, minimize
   unfilled priority-1 product units, then priority-2 units, then incremental cost.
   Equal-priority backlog ties use descending production-order ID in the fixture
   oracle; a production optimizer can establish a different documented tie-break.
9. Existing POs remain active after recovery purchases. A separate routine PO
   supplies 900 controllers on Nov 9 to cover later demand in the no-delay control.
   The delayed original PO also arrives Nov 9 in the disruption scenarios. The
   baseline ends with 1,500 excess R2 controllers at A; do not hide that surplus.

## Use from the app

```python
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[2]  # If called from backend/app/<file>.py
path = repo / "sample_data/generated/baseline_delay/dataset.json"
snapshot = json.loads(path.read_text(encoding="utf-8"))
metadata = snapshot["metadata"]
inventory = snapshot["tables"]["inventory_snapshots"]
```

Use `manifest.json` to populate the scenario selector. Each scenario has the same
table schema and stable business IDs. Scenario changes need no data migration.

For Snowflake integration, `schema.sql` defines local development tables and the
NDJSON files provide corresponding rows. Select a dedicated development schema
before executing the DDL. No database or schema is selected by the script and no
data has been loaded. Loading/staging is a later integration step. Scope every
join by `scenario_id` as well as the business ID; the same IDs intentionally
repeat in separate scenarios. Repeated loads would duplicate rows, so use an
empty target or implement an explicit reload. Validate locally rather than
assuming Snowflake standard-table key constraints will enforce integrity.

## Document extraction versus planning tests

For planning tests, each snapshot already contains the current normalized facts
and links them to source documents. The messages and snapshots agree by design.

For an extraction/replanning test:

1. Start from the baseline tables and the relevant original quote.
2. Supply only `DOC-QUOTE-B-UPDATE.txt` from the reduced-capacity scenario.
3. Have the agent extract the two new lots, deadlines, fees, and supersession.
4. Apply the extraction to a copy of the baseline and rerun planning.
5. Compare against the reduced-capacity snapshot and separate test answers.

Do not give the agent both the updated structured offer and ask it to prove it
extracted that same offer from prose. Keep `tests/expected_*.json` out of runtime
retrieval and prompts. Historical quotes remain in the documents, but current
rows reference their replacements. The extraction targets include literal source
spans to make citation checks possible. No document parser has been implemented.

## What the checks cover

Schema/types, primary and foreign keys, duplicate inventory, PO lot totals,
customer/production consistency, document supersession, evidence timestamps,
revision matching, donor headroom, partial fulfillment, fees, and full-BOM daily
balances. Email checks verify attribution, unique IDs, reply links, and parsed TXT
headers/bodies against the snapshots. Exports are compared to JSON snapshots; checksums and deterministic
regeneration are verified. The small reference solver is also compared with an
independent exhaustive search on reduced integer cases.

The reference oracle is deliberately limited to this sample's one constrained
component, destination, deadline, independent lots, and zero minimum orders. It
is a test helper, not the production optimizer. Its output and validation do not
establish agent performance, general network optimization, or real-world savings.

## Later: presentation data

Only the development profile exists today. Once the workflow is working, extend
the generator with a separate presentation profile, more background orders,
additional disruption cases, and reviewed demo narratives. Preserve these small
fixtures as regression tests. More rows alone should not change the decision;
new constraints should have independently verified outcomes. Keep all generated
company data and correspondence labeled synthetic in the presentation.
