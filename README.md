# sumo

> **Active data pack:** [NorthStar happy path V2](data/northstar_happy_path/README.md),
> with the manager-driven 80-transfer + 40-purchase hybrid scenario and eight role-play stages.
> Previous datasets and acceptance outputs are preserved in [archive_data](archive_data/README.md).
> This is a data-only update. The application described below still uses the retired
> contract and needs a new adapter before use; its former local fixture paths are
> intentionally empty. Snowflake migration is deferred, and no cloud data was changed.

A local supply-chain investigation and recovery application built with FastAPI, React, a deterministic planning engine, and Snowflake-hosted Claude. It follows the supplied workflow: dated evidence → Operations and Sourcing investigations → independent Qualification review → recovery evaluation → human decision → revalidation → review-only action drafts.

The offline demo uses synthetic evidence and scripted specialists. New live assessments default to managed Snowflake Cortex Agent objects with scoped, typed application tool callbacks. Provision the dedicated objects before starting a new live assessment; see [managed Agent setup](snowflake/README.md#managed-cortex-agents). A live failure never falls back to local fixtures or another model provider. Earlier saved assessments used direct `AI_COMPLETE` calls and retain that provider label; they do not acquire native execution records retroactively.

## Start locally

Use Python 3.12, uv, and Node.js 22.12 or newer (verified on Node.js 24). Run from the repository root:

```sh
uv venv --python 3.12 backend/.venv
uv pip sync --python backend/.venv/bin/python backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
bash scripts/dev.sh
```

Open [the local console](http://127.0.0.1:5173). The API is available at [localhost:8000](http://127.0.0.1:8000), with [interactive API documentation](http://127.0.0.1:8000/docs). The launcher starts both local services and stops them together. Offline assessments require no Snowflake credentials. The console's connection check may report Snowflake unavailable while the offline workflow remains usable; it does not start model inference.

The console opens in **Risk Inbox**, with severity filters, search, and scenario-derived impact indicators. Select **View analysis & recovery options** to open the offline decision workspace, or use **New assessment → Offline scenario → Concurrent specialists**. The six-step workflow follows **Risk alert → Analysis → Investigate → Compare → Decision → Action**. Company Brain answers supported offline questions from the current snapshot; live questions use the existing Snowflake Analyst surface. Use **Add new scenario** to combine exact qualified source quantities and compare the engine’s cost, shortage, schedule, and authority checks. Only feasible scenarios within authority can be selected. The initial investigation deliberately stops at an evidence wait. Review the source requests, then select **Load demo confirmations** to admit the scenario's dated source responses. The engine evaluates supported plans and freezes the recommendation. Enter a reviewer name and rationale to approve, choose an alternative, or defer. Approval revalidates the exact plan before preparing `DRAFT_NOT_SUBMITTED` artifacts. The separate **Outcome evaluation** page replays held-out synthetic events only after a forecast is frozen.

For the supplied recovery scenario, the engine derives 300 required drivers, 180 initially usable, and 120 uncovered. Recovery must supply 60 cumulatively before Thursday's production start and 120 before Friday's. Warehouse on-hand is 100, while only 40 are transferable. After the required source confirmations, the cost-first plan is transfer 40 + Express 20 + Standard 60 for **$1,220**. A supported Reserve fallback costs **$1,480**. The existing **$2,400** primary PO obligation stays separate from incremental recovery expenditure.

Every workflow step includes a **Snowflake** link and a context panel explaining the cloud resources used. The panel links to governed data, evidence, Query History, and audit records, with copyable read-only lookups for the current case. Cortex Search and Analyst links open the configured service and semantic view directly. Offline cases label these as reference links and do not export their activity. Console links use the configured account; accounts using a locator can set `SNOWFLAKE_SNOWSIGHT_URL=https://app.snowflake.com/organization/account`. Generating links does not authenticate or query Snowflake.

## Live Snowflake setup

Keep credentials in the private, gitignored `backend/.env`. Preserve any existing file; copy `backend/.env.example` only when creating a new configuration. The account, database, warehouse, app role, Search service, semantic view, and supported Claude model are configurable there. SQL authentication and REST authentication are separate: SQL can use password, PAT, keypair, or browser authentication; Search and Analyst use an explicit `SNOWFLAKE_REST_AUTH` route: `session` uses the configured SQL login and its short-lived session token; `pat` uses `SNOWFLAKE_TOKEN`. Authentication failures never switch routes automatically. REST requests explicitly carry the dedicated application role in `X-Snowflake-Role`.

Read the [Snowflake setup and verification guide](snowflake/README.md) before applying cloud assets. The implementation session provisioned the dedicated `DEFAULT_DATABASE.SUPPLY_RECOVERY_V1` and `DEFAULT_DATABASE.SUPPLY_RECOVERY_V1_EVAL` synthetic schemas and loaded an initial snapshot under explicit user authorization. Reproducible setup remains available for a fresh environment. Search can be intentionally suspended after validation; an SQL connection badge does not establish that Search or Analyst is available. Refer to the [acceptance report](docs/acceptance/report.md) for independent, final verification statuses and limitations.

Live human decisions also require `PROJECT_LIVE_REVIEWER_ID` and `PROJECT_LIVE_REVIEWER_TOKEN` in the backend configuration. The reviewer ID must match the decision actor; the console sends the token in `X-Reviewer-Token` and keeps it only in browser memory.

## Verification

Run backend tests from `backend/` so the private configuration resolves consistently:

```sh
cd backend
.venv/bin/python -m pytest -q
```

The suite includes a real local HTTP process restart test, so its execution environment must permit local socket binding and subprocesses. It covers deterministic calculations, evidence and source isolation, invalid candidates, stale decisions, budgets, durable waits, idempotent I/O, and evaluator isolation. Frontend verification is `npm --prefix frontend run build` from the repository root, followed by the browser workflow in the runbook.

Generate the matched offline architecture comparison and declared delay sensitivity report without cloud inference:

```sh
cd backend
.venv/bin/python -m app.evaluation.benchmark --output ../docs/acceptance/offline-comparison.json --repetitions 3
```

This compares deterministic, single-agent, sequential-specialist, and concurrent-specialist execution with matched evidence, policy, and run-wide limits. Scripted latency and synthetic plan quality do not establish hosted-model performance or multi-agent superiority. Delay sensitivity points have no empirical probability weights.

## Files and operating boundaries

| Path | Purpose |
| --- | --- |
| `backend/app/` | Typed contracts, source validation, deterministic engine, specialists, workflow, API, and durable checkpoint owner |
| `backend/data/initial_case.json` | Planner-visible synthetic initial source package |
| `backend/evaluator_data/` | Separate held-out replay data; never specialist evidence |
| `backend/.runtime/` | Gitignored local SQLite checkpoints, artifacts, operation receipts, and export outbox |
| `frontend/src/` | Responsive assessment, evidence, specialist activity, decisions, drafts, and outcome console |
| `snowflake/` | Versioned schemas, roles, governed views, Search/Analyst assets, source loader, and independent checks |
| `docs/runbook.md` | Configuration, demo walkthrough, API contracts, recovery, and validation procedures |
| `docs/workflow.mmd` | Diagram-aligned Mermaid workflow, including evidence loops and the separate evaluation branch |
| `docs/acceptance/report.md` | Sanitized acceptance evidence and per-surface live verification statuses |
| `sample_data/` | Separate pre-existing development fixture family and its own documented rules |

The active 300-driver scenario and the separate `sample_data/` fixture family use different business quantities and rules; do not combine their stock or expectations. Historical reference material under `docs/supplyrelay-handoff/` is documentation, not a runtime implementation dependency.

The project runs locally. It does not deploy, push changes, send supplier messages, place orders, move inventory, or change customer commitments. Every generated purchase, transfer, and evidence-request artifact remains a review-only draft.

## Persistent cases and scenario revisions

The workspace now keeps cases across multiple assessments, derives risk alerts from
evidence, compares versioned scenarios with receipt/MOQ/price/capacity/policy assumptions,
and shows per-customer delivery and projected financial impact. Hypothetical scenarios
remain ineligible for approval. Company Brain can prepare grounded scenario proposals
for review. The existing deterministic arithmetic and live evidence boundaries remain.

After approval, **Action** supports explicit submission to a **local simulator** and
dated transaction updates. Material changes start a new assessment in the same case;
idempotent receipt events update synthetic inventory with their actual receipt times.
Real orders and transfers remain unsubmitted. See the [lifecycle walkthrough and API
reference](docs/lifecycle-v2.md) for the full workflow, migration, and integration limits.
