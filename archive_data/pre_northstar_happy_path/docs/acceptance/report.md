# Implementation acceptance

## Subsequent live recheck — October 23, 2026, 2:22 PM Pacific

The requested live rerun used the existing initial and confirmed Snowflake snapshots without republishing source data. Case `SR-1D01A698` reached `WAITING_DECISION` with the $1,220 recommendation and $1,460 / $1,480 alternatives. SQL evidence validation, scoped Search, hosted Claude specialists, Cortex Analyst (including the browser flow), a fixed warehouse demand aggregation, and audit delivery passed. All 82 audit records arrived with unique IDs and zero pending exports. This rerun used 11 hosted Claude calls and two Analyst requests.

Search is now **active and left running for live use**, with ongoing Snowflake service charges. This supersedes the suspended Search and failed Analyst statuses in the original implementation record below. Two fixes were verified: accepting Snowflake's `ACTIVE` serving state and retrying transient busy-checkpoint responses during browser polling. The frontend build, 155 backend tests, and 18 separate sample-data tests passed. Live human approval and cloud outcome replay remain unverified because reviewer configuration and separate evaluator credentials are absent. Existing source data also lacks customer/commercial terms; those impact values correctly remain unknown.

The sanitized [live recheck record](live-recheck-2026-10-23.json) contains the exact run, snapshot, computations, results and limitations. The following sections retain the original implementation-session evidence and budgets.

Verified on 2026-10-23 on branch `dev/implementation-v1`. Changes are local and have not been pushed. The pre-existing `.idea/` directory was preserved. No Git credentials, remotes, account network policies, or model-region settings were changed.

## Result and diagram alignment

The project implements the supplied diagram in the existing FastAPI/React repository. [The Mermaid workflow](../workflow.mmd) includes the investigation, qualification, deterministic planning, human decision, evidence loop, escalation, and separate frozen-outcome evaluation branches.

The initial snapshot yields **300 required drivers, 180 usable, and 120 uncovered**. The time-phased requirement is 60 cumulative recovery drivers before Thursday's production start and 120 before Friday's. Warehouse on-hand is 100; allocations leave only 40 transferable. Missing supplier reservation/receipt/expiry confirmations produce three precise evidence requests and a durable wait. The wrong-revision budget offer is rejected explicitly.

After explicit trusted source publication, the engine enumerates 2,442 complete feasible quantity combinations, including 23 within the $1,500 authority limit. The cost-first recommendation is **transfer 40 + Express 20 + Standard 60 = $1,220**. Alternatives include a $1,460 combination and the supported **Reserve 100 + transfer 20 = $1,480** fallback. The existing **$2,400 primary PO obligation remains separate**. Quantities, schedules, costs, and predicates come from deterministic tools; hosted models cannot set them.

Operations and Sourcing discovery run with at most two workers. Quantity-dependent evaluation waits for validated Operations results. Qualification independently recomputes predicates and checks scoped Search evidence; its fixed checklist tools run before one constrained hosted review submission. These are application-controlled Claude role loops, not native managed Cortex Agent resources.

Recommendations and forecasts freeze before evaluator data is opened. A selected alternative freezes separately. Human selection is bound to the exact run, company, scope, snapshot, policy and checkpoint. Revalidation checks current source publication, feasibility, deadlines, reservation expiry and authority. New evidence invalidates prior selection and drafts. Deferral preserves the original real deadline and budgets. Purchase, transfer and evidence-request artifacts remain `DRAFT_NOT_SUBMITTED`; no business action is executed.

## Independent Snowflake checks

`live_verified` means the named service actually responded and passed application validation. Mocked tests do not establish live availability.

| Surface | Final status | Evidence and boundary |
| --- | --- | --- |
| Operational SQL and immutable evidence | **live_verified** | [SQL result](live-sql.json): the confirmed snapshot contains 24 documents, with source hashes, membership and fingerprint validated. Both the initial and explicit confirmation snapshots were published. Runtime reads governed Snowflake rows; it does not substitute fixtures. |
| Cortex Search | **live_verified; now suspended** | [Search result](live-search.json): scoped retrieval resolved citations against authoritative SQL records. Actual specialist runs also used Search. [Final suspension](search-suspension.json) confirms both `INDEXING_STATE` and `SERVING_STATE` are `SUSPENDED`. App-role preflight returned `SEARCH_PAUSED` with zero hosted-model calls after suspension. |
| Snowflake-hosted Claude | **live_verified** | [Independent smoke check](live-claude.json) passed. The [initial live workflow](live-workflow-initial.json) reached `WAITING_EVIDENCE`, and the [confirmed live workflow](live-workflow-confirmed.json) reached `WAITING_DECISION` with the $1,220 / $1,460 / $1,480 plans. All three specialist findings identify Snowflake-hosted Claude and their actual tools. |
| Semantic view / Cortex Analyst | **failed last live check** | The focused semantic view was provisioned and the service returned a response, but application SQL review rejected it: [record](live-analyst.json). Two approved requests were consumed. The parser now accepts ordinary comments and one trailing semicolon, with local tests, but that correction has **not been verified live**. Returned SQL is never executed. |
| Application audit persistence | **live_verified** | [Persistence result](live-persistence.json): identical event ID retried successfully. Both live workflow checkpoints report zero pending audit exports. Findings, qualification, plans, freezes, evidence requests, activity and checkpoint metadata use one idempotent outbox owner. |
| Evaluator cloud events and scored persistence | **not run live; mocked locally** | A separate evaluator token was unavailable. Evaluable feeds, scoped reads, immutable IDs/hashes, attribution checks and result export are implemented and tested. The evaluator-only INSERT grant added to setup assets after provisioning has not been applied live. |
| Native managed Cortex Agents | **not implemented / not claimed** | No managed-agent resource is represented by the application-controlled inference loops. |

The approved cloud setup created `DEFAULT_DATABASE.SUPPLY_RECOVERY_V1`, its isolated `_EVAL` schema, and the three `PROJECT_RECOVERY_*_V1` roles, with owner/application grants to the configured user. The application and Search-owner roles have no evaluator-schema grants. Runtime forces the dedicated application role even when the configured setup context is broader. REST explicitly uses the selected SQL-session authentication route; no authentication or provider fallback occurs.

The session used **25 Claude calls total**: one 128-output-token smoke call and 24 workflow calls with a 512-output-token ceiling. The workflow count includes bounded repairs and recovery attempts; completed paid receipts were retained across retries. Search stayed within the approved 25-query total ceiling. Two Analyst requests were approved and used. Usage metadata is available for only some responses; total token use and dollar cost are unknown and are not presented as zero. No further inference or Analyst requests were made after those limits.

Search remains paused to stop ongoing indexing/serving charges. Future live investigations require an explicitly authorized resume and recheck, described in the [Snowflake guide](../../snowflake/README.md). SQL source and audit access remain configured. The live acceptance store is the gitignored `backend/.runtime/live-acceptance.sqlite3`; the regular local console uses its separate default store. The original live run's 30-minute deadline is not renewed by restarting it; after expiry, create a new assessment within an authorized service budget.

## Local acceptance

| Check | Result |
| --- | --- |
| Complete backend suite | **120 passed**, including a real HTTP process restart at evidence-wait and decision-wait checkpoints. One third-party AnyIO deprecation warning. |
| Pre-existing `sample_data` suite | **18 passed**; the separate fixture family's quantities were not combined with the active scenario. |
| Backend and Snowflake-script lint | Passed using `ruff check --config pyproject.toml app tests ../snowflake`. |
| Frontend production build | Passed on Node.js 24 with Vite 8.3.5. |
| Frontend dependency audit | **0 advisories** at verification time. |
| Browser workflow | Verified initial evidence wait, source citations, demo confirmation admission, feasible alternatives, approval, defer/resume, Reserve alternative selection, review-only purchase/transfer drafts, actual activity and frozen-outcome replay. |
| Source isolation and failure cases | Tests cover missing/contradictory evidence, allocations, wrong revision, expiry, shared capacity, stale approval, newer snapshot invalidation, concurrency, uncertain paid I/O, immutable writes and hidden-event isolation. |

The browser's approved recommendation replay yields **150 on-time robots, 5 whole orders, zero remaining shortage, $1,220 incremental expenditure, and +60 on-time robots versus the 90-unit no-recovery baseline**. It reports zero nominal forecast error. This is deterministic replay of held-out synthetic receipts, not observation of executed business actions.

Screenshots: [assessment](console-assessment.png), [review-only drafts](console-drafts.png), [frozen outcome replay](console-outcome.png).

## Matched architecture comparison

[The reproducible offline report](offline-comparison.json) runs deterministic, same-tool single agent, sequential specialists and concurrent specialists three times each, using matching evidence fingerprints, policy, shared limits and fresh per-run caches. All four produce the same validated $1,220 plan, 150 on-time robots, five whole orders and zero shortage. Recorded registered tool calls across the initial/released assessment are 0 / 8 / 9 / 9 respectively; the deterministic pipeline records engine activity separately. Concurrent specialist activity actually overlaps; sequential and single-agent activity does not.

Median application times on this machine were approximately 0.47 / 0.52 / 0.51 / 0.52 seconds. These scripted timings include local storage and deterministic computation, not hosted-model latency. They do not demonstrate multi-agent superiority or model quality. The report also includes 16 declared zero/one-business-day source-delay sensitivity points, with no probability or reliability weights. Hosted-model usage and cost for this offline comparison are null.

## Remaining operating limits

- Analyst needs a newly authorized live recheck of the corrected SQL review parser. Cloud evaluator replay/export needs separate evaluator credentials and its explicit event publication/grant.
- Live recommendation, source retrieval and audit export were checked; live human approval/draft preparation was not exercised with a configured live reviewer token. Those paths passed local API/domain tests and offline browser acceptance.
- Source confirmations use a trusted normalized synthetic feed. This does not establish performance for extracting authoritative facts from arbitrary supplier prose.
- The implemented policy is versioned `cost-first-v1`. No unapproved resilience preference or empirical supplier reliability probability is implied.
- The app is a local implementation with local reviewer authorization. Production deployment, multi-user identity integration, business execution and Git push are outside this session's delivered state.

Installation, recovery procedures, exact configuration and API payloads are in the [runbook](../runbook.md). All credentials remain in private ignored configuration; reports and screenshots contain sanitized synthetic evidence only.
