# NorthStar live workspace

The original Risk Inbox, six-step decision workflow, analysis tabs, comparison
table and Company Brain are connected to the NorthStar V2 data. The active
provider is Snowflake; connection failures return an error and never select a
local fixture or another model provider.

## Cloud data and state

`DEFAULT_DATABASE.NORTHSTAR_RECOVERY_V2` contains 25 typed `SOURCE_*` tables
with 205 records, five immutable `PUBLISHED_SNAPSHOTS`, ingestion metadata,
`CASE_STATE`, versioned `AI_FINDINGS` and `AUDIT_EVENTS`. Existing legacy schemas
and local checkpoints are preserved. The existing application role gets read
access to the evidence and scoped writes to case state, findings and audit.

Run these commands from `backend/` using the private existing `.env`:

```sh
# Validate locally; no cloud calls.
.venv/bin/python ../snowflake/load/northstar.py
# Idempotent typed-table upserts and snapshot publication; preserves case decisions.
.venv/bin/python ../snowflake/load/northstar.py --apply
# Verify every ingested row, runtime calculations and current-version findings.
.venv/bin/python ../snowflake/verify/northstar.py --live
# Explicitly run missing specialists and a Company Brain check.
.venv/bin/python ../snowflake/verify/northstar.py --live --investigate --chat
```

Runtime planning reads only cloud-published stages 0–4, starting at the complete
stage-4 evidence package. The full authoring catalog is used only by ingestion
and verification. Scripted approval/draft examples and monitoring rows are
stored separately from published planning evidence. They cannot approve a plan.
Monitoring must be explicitly admitted after the actual reviewer approves C.

Evidence hashes, scenario IDs, stage bounds and document dates are validated
before calculation or inference. Quantities and USD cents are integers. The
engine consumes the full BOM, respects one shared production line and two
business days to shipment, and allocates contracts before forecast. Offers and
reserved pilot stock are counted only in selected hypothetical plans. R&D stock
requiring inspection is excluded.

A real reviewer decision binds the exact fingerprint and scenario version,
checks the $250,000 recovery limit, and atomically updates cloud case state and
its audit trail using a revision predicate. Drafts stay unsent. No external
purchase, email, stock movement or customer commitment is executed. This is a
synthetic recovery case with live cloud I/O; dates remain the evidence's scenario
dates, not the wall clock. The original $164,800 PO is tracked separately.

## Claude analysis

Operations, Sourcing and Qualification call Snowflake `AI_COMPLETE` with the
configured Claude model. Company Brain uses the same hosted model and scoped
cloud evidence. These are real hosted inference calls; this path does not claim
native Cortex Agent objects, Cortex Search or Cortex Analyst execution.

The prompt excludes duplicate email bodies and authoring-only tables. The
structured citation schema enumerates only documents in the current snapshot.
The app checks returned citations, rejects unsupported currency amounts and blanket
forecast claims, and keeps model text advisory: deterministic
calculations own spending, schedules, inventory and approval eligibility.
Findings retain model, query ID, token usage, snapshot and prompt version in
Snowflake. A prompt change invalidates older cached findings. Completed reviews
survive another specialist failing; rerunning invokes only missing reviews.

`PROJECT_NORTHSTAR_MAX_COMPLETION_TOKENS` defaults to 1200, bounded to 256–4096.
The previous general 512-token cap truncated some structured specialist responses;
the NorthStar output setting is separate. No automatic paid retry is performed. The recorded
[live verification](northstar-live-verification.json) includes source-row equality,
three specialist query IDs, a Company Brain query, and a rolled-back live draft
transaction that leaves the user case unchanged.
See the official [AI_COMPLETE reference](https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string)
and [structured-output contract](https://docs.snowflake.com/en/sql-reference/functions/ai_complete-structured-outputs).

## API and local verification

- `GET /api/northstar/case`: cloud case, evidence, calculations and current findings.
- `POST /api/northstar/case`: `revision`, `snapshot_id`, `action`; approval adds
  `actor`, `reason` and `plan_id`. Supported actions: `approve`, `monitor`, and
  stage `advance` when a case is at an earlier planning stage.
- `POST /api/northstar/investigate`: current `revision` and `snapshot_id`.
- `POST /api/northstar/chat`: the same binding plus `question`.

Stale bindings return 409. Cloud/model failures return 503. The frontend exposes
actual provider state and per-request failures; it never relabels a local case live.

`PROJECT_NORTHSTAR_MODE=offline` explicitly selects the isolated local provider
for development and regression tests only. It uses the same deterministic engine
and is not a fallback. The default is `live`.

Backend tests cover cloud corruption/missing evidence, future rows, citation
allowlists, deduplicated context, partial inference failures, atomic cloud
approval and competing writers, alongside the existing engine/security suite.
The HTTP restart test uses explicit offline test configuration. No credentials
or model calls are required for unit tests.

The frontend is `frontend/src/NorthStar.jsx` and reuses the original UI primitives
and styles. Run `bash scripts/dev.sh` from the repository root; open
[the local workspace](http://127.0.0.1:5173). The API uses `backend/.env` and the
existing `VITE_API_URL` support is preserved. Hosting must configure Snowflake
credentials privately in the backend; local verification does not deploy them.
