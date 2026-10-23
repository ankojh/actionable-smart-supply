# Decision cases, scenario versions, and simulated execution

The existing React/FastAPI application now keeps an operational **decision case**
across multiple bounded assessments. Its planning graph has explicit investigation,
reconciliation, business-impact, and plan-evaluation stages. Operations and Sourcing
retain their concurrent specialist paths and independent Qualification review.

## Walkthrough

1. In Risk Inbox, select **Scan simulated supply feed**. The API derives alerts
   from the current evidence, validates the primary supplier change, records an
   idempotent event, and opens or updates a case. Repeating an unchanged scan does
   not create another assessment. The button scans locally; an optional local file
   watcher can also ingest simulated events automatically (configuration below).
2. Load the dated demo confirmations. Review inventory, time-phased material needs,
   internal recovery feasibility, and qualified external or blended alternatives.
3. In **Impact Analysis**, inspect per-customer delivery impact and projected revenue
   exposure and penalties. The offline fixture adds explicitly fictional commercial
   terms. Live snapshots without those terms display unknown financial values.
   Revenue exposed to delay is not realized revenue loss. Penalties use calendar days.
4. In **Compare**, create a scenario. Change exact source quantities, or let the
   existing bounded engine optimize quantities. Optional assumptions include receipt,
   MOQ, order multiple, source maximum, price, freight, capacity, and spending limit.
   A transfer supports receipt and transfer-charge assumptions. Every saved version
   preserves its parent, base snapshot, full inputs, evaluated plan, business impact,
   and differences from its parent or the original recommendation. Parent selection
   establishes comparison lineage; inputs are complete replacements, not hidden patches.
5. Company Brain also accepts grounded commands such as **Try transfer only**,
   **Try Express Supply 20, Standard Supply 60, transfer 40**, or **What if Express
   Supply ETA is 2026-11-06T08:00 and budget is 1800**. This is a bounded command
   parser, not a claim of unrestricted natural-language planning. Review the proposed
   inputs in the scenario editor before simulation. Live investigative questions
   continue to use the separate read-only Analyst surface.
6. A scenario using changed factual or policy assumptions is **hypothetical**. Its
   derived snapshot and plan never enter the approval-eligible plan list. Confirm
   source changes through the authoritative evidence path before approving them.
   Quantity-only scenarios using verified terms remain eligible when feasible and
   within the actual spending authority. Choosing `internal_first` for optimization
   prefers a complete, affordable internal plan when one exists; the operational
   recommendation retains the explicitly versioned `cost-first-v1` policy.
7. Approve a verified plan. Existing freshness, reservation, authority, and exact-plan
   binding checks run before review-only drafts are generated. On **Action**, enter
   a reason to **Submit approved plan to simulator** as the approving reviewer.
8. Record simulated acknowledgments, shipping, receipt, delay, failure, or cancellation
   updates. The API validates transaction transitions, event order, and case revision.
   Transaction IDs and receipts survive restart. Repeating an event cannot double-receive
   inventory. Received transfer stock consumes unallocated donor inventory; purchase
   receipts reduce the remaining confirmed source quantity. Receipts are time-phased,
   so late stock cannot retroactively rescue earlier production starts.
9. A material execution change invalidates the previous assessment's approval and
   drafts and starts a new assessment **inside the same case**. Transaction identities
   and history remain visible. Outstanding transactions must be reconciled or cancelled
   before approving replacement recovery. `RECEIPTS_COMPLETE` means all simulated
   recovery receipts were recorded, not that production or customer delivery occurred.

The case survives the assessment's 30-minute computation limit. **Reassess this case**
creates a new bounded assessment using the latest case evidence. It supersedes the old
assessment and preserves its frozen forecasts and scenario artifacts for audit.
Existing installations gain case records through an additive SQLite migration.

## API additions

| Route | Purpose |
| --- | --- |
| `GET /api/cases` | Persistent cases and assessment/transaction references |
| `GET /api/cases/{id}` | Current case revision, evidence, events, and transactions |
| `POST /api/cases/{id}/assessments` | Reassess with `{revision}` |
| `POST /api/monitor/scan` | Scan the local synthetic feed and deduplicate a supplier event |
| `GET /api/monitor/status` | Local watcher configuration and sanitized activity counters |
| `POST /api/risk-events` | Report an unverified risk or admit an explicitly simulated supplier event |
| `POST /api/graph/runs/{id}/scenario` | Save a complete versioned scenario with optional parent and assumptions |
| `POST /api/graph/runs/{id}/scenario-proposal` | Read-only grounded scenario command; requires current snapshot/checkpoint |
| `POST /api/cases/{id}/dispatch` | Explicitly submit the approved plan to the simulator |
| `POST /api/cases/{id}/execution-events` | Admit a simulated transaction event and replan when material |

Inputs are strict typed contracts in `backend/app/lifecycle_models.py`. Risk events
identify the PO, part, receiving location, dated source reference, source type, and
changed receipt/quantity. An `inventory_change` supplies physical plant on-hand stock;
it cannot reduce it below existing allocations and currently requires one unambiguous
stock record. Manual reports never update authoritative facts or invalidate approvals.
Simulated source changes and dispatch are rejected for live cases. Live source updates
still require the existing governed Snowflake publication/admission process.

For automatic local monitoring, set `PROJECT_SIMULATED_FEED_PATH` to a JSON file
containing a list of `RiskEventInput` objects and restart the API. Each event must
explicitly use `source: "simulated_supplier"`. The watcher scans at startup and every
`PROJECT_MONITOR_INTERVAL_SECONDS` (default 10 seconds), admits validated new events,
and advances offline assessments. Durable event receipts prevent repeats across
restarts. The file is bounded to 100 events and one megabyte. Keep event IDs immutable;
append a new ID for a new source change. Leave the path unset to disable automatic
monitoring. This adapter makes no cloud calls or hosted-model requests. Rejected
events produce sanitized status messages and are retried on later scans.

Dispatch requires case revision, exact run/snapshot/checkpoint, approving actor,
and a reason. The only adapter is `simulated`. Its deterministic transaction IDs and
atomic SQLite writes implement a durable local submission boundary without external
I/O. This does not claim a real ERP adapter or distributed exactly-once delivery.
Risk event IDs are immutable; execution retry comparison ignores only the transport
revision, allowing a completed event to be retried after the case advances.

## Operating boundaries

- No real supplier discovery API, RFQ delivery, PO submission, inventory movement,
  cloud polling, deployment, or Snowflake provisioning is performed by this change.
  Live cases retain review-only drafts and governed evidence isolation.
- The active planner still targets the existing single-part robot scenario and its
  shared production line. General multi-component BOM optimization and multi-tenant
  identity are separate extensions; the historical `sample_data` family is not mixed in.
- Execution events model whole-action receipts. Partial receipts, production actuals,
  customer delivery actuals, and real procurement cancellation require additional
  adapter/domain contracts. Cancelled or failed simulated supplier reservations are
  removed from available capacity until a new authoritative confirmation is admitted.
- Financial exposure uses only commercial terms supplied with the current snapshot.
  The separate held-out outcome evaluator remains isolated from all operational events.
- SQLite remains the local lifecycle owner, including case/event receipts. Snowflake
  remains the governed live evidence and audit system. No new cloud schema is required
  for the local simulation features.

## Validation

Run backend tests with `.venv/bin/python -m pytest -q` from `backend`, with loopback
socket permission for the existing real-server restart test. `tests/test_lifecycle.py`
covers persisted case continuity, stale decisions, event deduplication, source trust,
scenario lineage and hypothetical isolation, per-customer/unknown financial exposure,
transaction idempotency, late receipts, replacement-order blocking, and legacy migration.
Build the frontend with `npm --prefix frontend run build` from the repository root.

Implementation verification: **150 backend tests passed**, including the real HTTP
restart test; backend/Snowflake-script lint, frontend production build, and diff
whitespace checks passed. Browser checks covered case creation, dated confirmations,
customer exposure, a saved hypothetical scenario with disabled approval, verified-plan
approval, simulated submission, and a retained supplier acknowledgment. See the
[execution-monitor screenshot](acceptance/lifecycle-v2-execution.png). No hosted-model
verification or real procurement transaction was performed for this change.
