# Runbook

> This runbook describes the retired scenario. Its data is preserved under
> `archive_data/pre_northstar_happy_path/`; local JSON inputs are no longer active.
> Use [the NorthStar data guide](../data/northstar_happy_path/README.md) and
> [role-play guide](../data/northstar_happy_path/ROLE_PLAY.md) for the new pack.
> Application adaptation and Snowflake migration are pending.

## 1. Local installation and launch

Use Python 3.12, uv, and Node.js 22.12 or newer (verified on Node.js 24). From the repository root, create the backend environment and install the pinned dependencies:

```sh
uv venv --python 3.12 backend/.venv
uv pip sync --python backend/.venv/bin/python backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
bash scripts/dev.sh
```

The console is [http://127.0.0.1:5173](http://127.0.0.1:5173); the API is [http://127.0.0.1:8000](http://127.0.0.1:8000). Vite proxies `/api` requests to the backend. `VITE_API_URL` can change the development proxy target. The launcher expects `backend/.venv` and `frontend/node_modules` to exist, binds both services locally, and shuts them down together.

To launch separately, use two terminals. In `backend/`:

```sh
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In `frontend/`:

```sh
npm run dev -- --host 127.0.0.1
```

Backend configuration is read from `.env` relative to its working directory. Run backend commands from `backend/`. Offline startup requires no cloud credentials. `GET /api/health` is local liveness only; `GET /api/snowflake/health` runs a small SQL query and returns a sanitized failure if Snowflake is unavailable. Opening the console does not start hosted model inference.

## 2. Offline end-to-end walkthrough

1. Select **New assessment**, **Offline scenario**, and **Concurrent specialists**. This explicitly uses local synthetic evidence and scripted specialist behavior. Other execution choices are deterministic pipeline, same-tool single agent, and sequential specialists.
2. Wait for `WAITING_EVIDENCE`. Operations derives requirements, available material, allocation exposure, and time-phased shortages. Independent Sourcing discovery examines alternatives. Quantity-dependent evaluation uses validated Operations output. Qualification identifies unsupported and unresolved predicates.
3. Inspect **Evidence library** or a source citation. Each source displays exact text, availability time, scope, source kind, and content hash. Missing or contradictory evidence produces a specific unsent evidence request and a durable wait.
4. Select **Load demo confirmations**. This offline-only control releases the scenario's supplied source confirmations into a new immutable snapshot. It does not invent supplier responses or send a request. The prior selection and drafts are invalidated, and affected investigations rerun; unchanged validated dependencies can be reused.
5. Wait for `WAITING_DECISION`. Confirm the derived results: 300 required drivers, 180 initially usable, 120 uncovered; cumulative recovery of 60 before Thursday's start and 120 before Friday's; 100 warehouse on-hand but only 40 transferable. The cost-first recommendation combines transfer 40, Express 20, and Standard 60 for $1,220. The supported Reserve fallback costs $1,480. The existing primary PO obligation of $2,400 remains separate.
6. Read qualified and rejected candidates, constraints, citations, plan expenditure, production outcome, and remaining gap. The policy is explicitly `cost-first-v1`; no unversioned resilience preference overrides that policy.
7. Enter a reviewer name and rationale. **Approve & revalidate** accepts the recommendation. Choosing another plan uses the `alternative` decision. **Defer assessment** records the reason and pauses at `DEFERRED` without action drafts.
8. An accepted selection rechecks current evidence, reservation deadlines, plan feasibility, and authority. If valid, it freezes the selected plan separately and reaches `DRAFTS_READY`. Review purchase and transfer drafts. Every draft is `DRAFT_NOT_SUBMITTED`; no business action occurs.
9. Open **Outcome evaluation**, choose a frozen recommendation or selected plan, and select **Replay offline outcomes**. Replay compares on-time units, whole orders, remaining shortage, expenditure, and change versus no recovery. Synthetic held-out data is opened only by the evaluator after freeze and never added to planner-visible evidence.
10. Inspect **Agent activity** for actual role starts/completions, tools, cache reuse, and engine checks. Concurrent Operations and independent Sourcing discovery are capped at two workers. Qualification is a separate role with its own tool requirements.

The scenario clock starts in November 2026 and advances by elapsed wall time or explicit source release, independently of the real deadline. Advancing scenario time does not extend the real deadline or replenish budgets. The complete flow and feedback paths are in [workflow.mmd](workflow.mmd).

## 3. Evidence ingestion and re-investigation

The console's **Supply dated response** records `human_clarification`. Include the response text, a source reference, and its availability time on the scenario clock. Human text is evidence, not automatically authoritative stock, reservation, supplier commitment, or purchasing authority. It cannot directly inject trusted structured claims.

Every response binds to the exact run URL, request, company, scope, snapshot, policy, and checkpoint. A stale response returns `409`; reload the latest run before deciding how to respond. Do not replace the checkpoint in an old approval to bypass reselection.

A live `supplier_confirmation` requires a separately published trusted successor source package and exact text/hash/claims/reference validation. The UI deliberately submits only human clarifications. See the [Snowflake guide](../snowflake/README.md) for the explicit synthetic source release path. Its source references use `synthetic-supplier-feed-v1/EV-*`. The initial loader does not open withheld confirmation data. Local offline fixture release is rejected for live runs.

Live human clarifications remain separately audited unverified notes; they cannot create an unpublished local source snapshot. Changed authoritative evidence saves a new immutable snapshot, clears the current decision, drafts, evaluated plans, and recommendation, and triggers the affected investigation. A new human selection is required. Unsupported options route back to Sourcing with explicit reasons. An incomplete plan routes to remaining-gap investigation; if no supported complete recovery remains, status is `ESCALATED` for human review. An unresolved candidate is not treated as usable supply.

## 4. Persistence, cancellation, and recovery

`backend/.runtime/project.sqlite3` is the default durable checkpoint store. It includes immutable artifacts, checkpoints, persisted budgets, operation receipts, create-request idempotency, and a live audit-export outbox. Run ownership also uses local lock files. Configure `PROJECT_STORE` to change the database location. Keep the database, its SQLite WAL/SHM files, and lock directory together when backing up a stopped local instance. Do not delete the runtime directory to repair a workflow.

Snowflake remains the live business-data authority. Local checkpoints coordinate execution and retain validated artifacts; they are not an alternative source of live inventory or supplier truth. The single application orchestration owner serializes per-run changes and flushes sanitized, idempotently identified audit records to Snowflake. A failed export retains the outbox for retry. `POST /api/graph/runs/{id}/sync` retries pending audit delivery without rerunning model inference.

| State or operation | Operator behavior |
| --- | --- |
| `CREATED` / `INVESTIGATING` | Poll the current run. Do not create a duplicate run to check progress. |
| `WAITING_EVIDENCE` | Supply a scoped dated reply or explicitly release offline demo confirmations. The wait survives process restart. |
| `WAITING_DECISION` | Review the frozen recommendation and current alternatives. This wait also survives restart. |
| `DEFERRED` | The decision is saved and execution pauses. Resume explicitly; it clears the prior decision and drafts before reevaluation. |
| `FAILED` | Read the sanitized error and activity; resolve its cause before resume. An uncertain external receipt needs operator reconciliation first. |
| `CANCELLED` | Current selection/drafts are cleared. Cancellation is terminal for that run; create a new assessment if work is needed. |
| `ESCALATED` | Investigate the remaining unsupported shortfall and obtain human review; do not treat it as a recovery recommendation. |
| Refresh | Bind to the current snapshot/checkpoint. A changed live snapshot invalidates current selection and drafts. Offline refresh explicitly clears selection/drafts and reevaluates. |
| Resume | Supported from `DEFERRED`, `FAILED`, `WAITING_EVIDENCE`, or interrupted `INVESTIGATING`; subject to the original deadline and remaining budgets. |
| `409 STALE_OR_BUSY_CHECKPOINT` | Reload current state; another owner may be active or the snapshot/checkpoint changed. |

Live model/tool operations first persist a `pending` receipt. If a process stops after an external call but before recording completion, completion is uncertain. The project refuses to blindly repeat that paid operation. Inspect the receipt and provider query/request records, reconcile the outcome through an authorized operator, and only then choose a recovery action. Do not reset or delete receipts, create duplicate calls, or assume a timeout means Snowflake did not execute.

Limits persist across waits and restarts: eight turns per role invocation, at most one output repair, three run-wide plan revisions, 72 run-wide model turns, 64 registered role tool calls, 12 engine evaluations, six refreshes, and a real 30-minute deadline. Only a new frozen recommendation consumes a plan revision; evidence waits and reuse of an unchanged recommendation do not. Output tokens are bounded by `SNOWFLAKE_MAX_COMPLETION_TOKENS`. Scenario-clock advancement does not reset any of these limits. An expired deadline requires a new assessment.

## 5. Live Snowflake configuration and setup

Keep secrets in `backend/.env`, which is gitignored; never put them in source, browser code, reports, screenshots, or command arguments. Preserve the existing private file. Use `.env.example` as a configuration reference.

| Setting | Purpose |
| --- | --- |
| `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` | Configured account and application identity |
| `SNOWFLAKE_AUTH` | SQL authentication: `password`, `pat`, `keypair`, or `externalbrowser` |
| `SNOWFLAKE_PASSWORD` / keypair fields | SQL credentials when that SQL authentication mode is selected |
| `SNOWFLAKE_TOKEN` | REST PAT when `SNOWFLAKE_REST_AUTH=pat`; also SQL PAT when SQL auth is `pat` |
| `SNOWFLAKE_REST_AUTH` | Explicit `session` or `pat` route; failures never switch authentication routes |
| `SNOWFLAKE_DATABASE`, `SNOWFLAKE_WAREHOUSE` | Target database and warehouse |
| `SNOWFLAKE_ROLE` | Setup context; runtime uses the dedicated app role |
| `SNOWFLAKE_APP_SCHEMA`, `SNOWFLAKE_APP_ROLE` | Defaults: `SUPPLY_RECOVERY_V1`, `PROJECT_RECOVERY_APP_V1` |
| `SNOWFLAKE_CORTEX_MODEL` | An account-supported Snowflake-hosted Claude model; configuration is not model availability proof |
| `SNOWFLAKE_SEARCH_SERVICE` | Default `EVIDENCE_SEARCH` |
| `SNOWFLAKE_SEMANTIC_VIEW` | Default `RECOVERY_ANALYTICS` |
| `SNOWFLAKE_REST_BASE_URL` | Optional official HTTPS account REST URL override |
| `PROJECT_LIVE_REVIEWER_ID`, `PROJECT_LIVE_REVIEWER_TOKEN` | Local authorized live decision identity and token |
| Evaluator user/token/role fields | Separate evaluator process identity; never use evaluator rights for application planning |

Select REST authentication explicitly: `SNOWFLAKE_REST_AUTH=session` uses the configured SQL login and a short-lived application-role session token; `pat` uses `SNOWFLAKE_TOKEN`. The client sends `X-Snowflake-Role: PROJECT_RECOVERY_APP_V1` (or the configured dedicated role). A role-bound PAT must permit that role. Do not change account model-region settings or introduce another provider after a failure.

During this implementation session, explicit user approval covered provisioning the dedicated synthetic schemas `DEFAULT_DATABASE.SUPPLY_RECOVERY_V1` and `DEFAULT_DATABASE.SUPPLY_RECOVERY_V1_EVAL`, creating the owner/app/evaluator roles, granting owner/app roles to the configured user, loading the synthetic initial snapshot, and bounded live verification. Final per-surface results and any intentionally suspended services are recorded in [acceptance/report.md](acceptance/report.md). Do not infer final Search, Analyst, or evaluation-persistence status from setup success.

For a fresh environment, follow [snowflake/README.md](../snowflake/README.md). Its setup script defaults to rendering SQL without applying it and omits Search unless explicitly included. Applying Search creates billable indexing/serving resources. Synthetic source tables and immutable manifest membership are versioned; the application role cannot publish authoritative source data. The evaluator schema is isolated from the application and Search-owner roles.

The initial live source snapshot intentionally lacks later supplier confirmations. Live progression requires trusted publication/admission of those later records, not the offline demo button. All live specialist roles run through application-controlled Claude loops with typed tools and independent application validation. No native managed Cortex Agent resource is claimed or required.

Cortex Analyst is a focused read-only investigative surface over `RECOVERY_ANALYTICS`. Its returned SQL is a review artifact and is never automatically executed. Approval-critical material arithmetic, receipt/calendar constraints, source predicates, scheduling, cost, and authority stay in deterministic snapshot tools.

## 6. Independent live verification

Run from `backend/`, within the authorized cloud-call budget:

```sh
.venv/bin/python ../snowflake/verify/check.py diagnostic --live
.venv/bin/python ../snowflake/verify/check.py sql --live
.venv/bin/python ../snowflake/verify/check.py search --live
.venv/bin/python ../snowflake/verify/check.py claude --live
.venv/bin/python ../snowflake/verify/check.py analyst --live
.venv/bin/python ../snowflake/verify/check.py persistence --live
```

`diagnostic` reports safe capability/authentication context. Each named check performs only its own surface: SQL source reads, one scoped Search query, one Claude call with a 128-output-token budget, one Analyst request, or one idempotent synthetic audit-record write repeated with the same event ID. Search can require time to index new rows; empty results fail explicitly. Evaluation persistence is a separate `evaluation-persistence --live` check with a separate identity.

Record SQL, Search, Claude, Analyst, audit persistence, and evaluation persistence independently as `live_verified`, mocked, failed, or not run. Mocked tests and configuration do not establish live verification. Use [acceptance/report.md](acceptance/report.md) as the session result ledger. A successful SQL health badge does not prove the other surfaces work. If Search has been suspended after validation to stop ongoing service cost, an authorized operator must restore it and reverify before a live workflow that requires Search.

## 7. API reference

Run JSON includes its exact status, checkpoint, scope, scenario clock, evidence snapshot, Operations/Sourcing/Qualification artifacts, evaluated plans, recommendation, evidence requests, activity, findings, human decision, drafts, frozen forecasts, budgets, error, and optional offline evaluation. The create and background-advancing endpoints return `202`; poll `GET` while status is `CREATED` or `INVESTIGATING`.

| Method and route | Payload / result |
| --- | --- |
| `GET /api/health` | Local API liveness |
| `GET /api/snowflake/health` | Safe SQL connection status |
| `GET /api/graph/runs` | `{runs: [...]}` summaries |
| `POST /api/graph/runs` | `{mode: "offline"\|"live", execution: "concurrent"\|"sequential"\|"single"\|"deterministic", policy_version: "cost-first-v1"}`; optional `Idempotency-Key` header |
| `GET /api/graph/runs/{id}` | Full durable run |
| `GET /api/graph/runs/{id}/events` | Actual recorded activity |
| `GET /api/graph/runs/{id}/drafts` | Review-only draft records |
| `POST /api/graph/runs/{id}/demo-confirmations` | Current `{snapshot_id, checkpoint}`; offline only |
| `POST /api/graph/runs/{id}/evidence` | Typed `EvidenceReply`, including exact request/scope/snapshot/policy/checkpoint and dated text/reference |
| `POST /api/graph/runs/{id}/decision` | Typed `HumanDecision`; live also requires `X-Reviewer-Token` |
| `POST /api/graph/runs/{id}/refresh` | Current `{snapshot_id, checkpoint}` |
| `POST /api/graph/runs/{id}/resume` | Current `{snapshot_id, checkpoint}` |
| `POST /api/graph/runs/{id}/cancel` | Current `{snapshot_id, checkpoint}` |
| `POST /api/graph/runs/{id}/evaluate` | `{frozen_id}`; offline replay or separately credentialed live evaluator |
| `POST /api/graph/runs/{id}/analyst` | `{question}`; live only, returns a review artifact |
| `POST /api/graph/runs/{id}/sync` | Retry pending sanitized Snowflake audit delivery |

`HumanDecision` contains `snapshot_id`, `checkpoint`, `policy_version`, `company`, `scope`, `action` (`approve`, `alternative`, or `defer`), `plan_id` (null for defer), `reason`, and `actor`. A recommendation can be approved only with its current plan ID; selecting another requires `alternative`.

`EvidenceReply` additionally contains `request_id`, a new `document_id`, `text`, `available_at`, `source_kind`, `source_reference`, and `claims`. Use `human_clarification` with empty `claims` for a manual response. Authoritative supplier admission has the stricter trusted-source path described above. Full typed definitions are in `backend/app/domain.py`; interactive route schemas are available at `/docs`.

## 8. Local acceptance and comparison

Run backend tests from `backend/`:

```sh
.venv/bin/python -m pytest -q
```

Permit local socket binding and subprocesses for the real HTTP restart test. It verifies both evidence-wait and decision-wait restoration across server processes, including requests, budgets, frozen plans, and alternatives. Other tests cover source filters/hashes, hidden-event isolation, quantity and revision traps, missing/contradictory evidence, reservation expiry, stale approval, concurrent operations, idempotent audit behavior, and uncertain paid-call receipts.

Build the frontend from the repository root:

```sh
npm --prefix frontend run build
```

Browser inspection should cover initial load without inference, the initial durable evidence wait, explicit offline confirmations, source citation text, a qualified alternative, approval and drafts, deferral/resume, activity, frozen replay, and a narrow viewport. Live start must remain an explicit choice. Review provider failures as sanitized messages rather than credentials or raw request dumps.

Run the offline comparison from `backend/`:

```sh
.venv/bin/python -m app.evaluation.benchmark --output ../docs/acceptance/offline-comparison.json --repetitions 3
```

The comparison uses the same initial/released evidence fingerprints, cost-first policy, fresh per-run caches, and budget limits for deterministic, single-agent, sequential-specialist, and concurrent-specialist execution. It reports measured application latency, actual role/tool events, overlap, quality, and freezes. Scripted runs have no hosted-model token cost; unknown usage/cost remains null. The declared zero/one-business-day sensitivity grid is unweighted and is not a real-world reliability probability model.

## 9. Boundaries

The project is a local implementation. It does not deploy, push Git changes, send evidence requests, email suppliers, purchase parts, move inventory, or change customer commitments. Purchase, transfer, and evidence-request artifacts remain `DRAFT_NOT_SUBMITTED`. A human decision authorizes preparing review-only drafts within this application, not executing business transactions.

Historical handoff source and the separate pre-existing `sample_data/` development scenarios are not runtime substitutes for the implemented 300-driver scenario. Held-out realized events and oracle/test labels stay evaluator-only and must never enter planning SQL, Search, prompts, or source snapshots.

## Evaluator-only cloud replay

`POST /api/graph/runs/{id}/evaluate` supports a live frozen forecast only when a separate evaluator identity is configured. `SNOWFLAKE_EVALUATOR_TOKEN` is mandatory; it never falls back to the application token. Validate the nominal source feed locally with `python ../snowflake/load/evaluation.py`; use its explicit `--apply` only with the evaluator role granted INSERT on `REALIZED_EVENTS`. The loader verifies content hashes and immutable event IDs. Live replay reads evaluator-only rows after checking the frozen forecast and exports scored metrics to `_EVAL.RESULT_RECORDS`; the application and Search-owner roles have no access to those tables. This session did not verify that separate identity.

To rerun the bounded live specialist acceptance, use `python ../snowflake/verify/workflow.py initial --live` then publish the explicit source confirmations and use `confirmed --live`. The durable acceptance store caps the combined run at 24 Claude calls, 512 output tokens per call, and 20 Search queries. Existing completed receipts and the original deadline survive `--resume`. Independent surface checks are additional to these counters; obtain an explicit total budget before rerunning paid verification.
