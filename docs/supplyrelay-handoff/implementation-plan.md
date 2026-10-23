# SupplyRelay implementation plan

## Scope and starting point

Build in `/Users/nikms/Documents/cmu/hackathon/actionable-smart-supply`, using its `backend/` FastAPI and `frontend/` React/Vite scaffold. This plan is for new implementation; the transfer itself changes documentation/reference files only. Existing Snowflake configuration is a connection seam, not fresh verification of account access, Search or Claude.

The first release is the fictional `shortage-recovery-v1` case, three bounded Claude investigators, deterministic recovery planning, a human decision and draft-only actions. Keep the cost-first objective as default. A resilience objective is a separately versioned policy, not a silent ranking change.

## Proposed code layout

```text
backend/
  app/
    main.py                  existing app; add routes and safe diagnostics
    config.py                existing configuration seam; explicit demo/live routes
    snowflake_client.py      existing connector seam; harden and bound reads
    domain/                  typed evidence, findings, plans, decisions and policies
    engine/                  calendars, material balances, schedules, cost and ranking
    sources/                 local/Snowflake snapshots, document retrieval, admission
    agents/                  Operations, Sourcing, Qualification and hosted-Claude adapter
    workflow/                LangGraph host, typed state, budgets and transitions
    storage/                 SQLite runs/checkpoints/receipts and audit outbox
    api/                     lifecycle, evidence replies, human decisions and drafts
    evaluation/              frozen-plan replay/scoring; separate evaluator inputs
  tests/
frontend/src/
  components/                findings, evidence, plans, timeline and draft views
  api/                       backend client and run-state polling
snowflake/
  setup/                     versioned schema, tables and scoped Search definitions
  load/                      reproducible fictional source import
  verify/                    independent SQL/Search/inference/export checks
```

Names can be adjusted to match the implementation. Keep one lifecycle owner and explicit planner/evaluator separation.

## Phase 1 — Contracts and deterministic engine

Agree on typed `EvidenceSnapshot`, `OperationsFinding`, `RecoveryCandidateSet`, `QualificationReport`, `EvidenceRequest`, `RecoveryPlanEvaluation`, `DecisionPacket`, `HumanDecision`, `FrozenRecommendation` and `OutcomeReport`. Include identity/scope, versions, hashes, source citations and event/availability timestamps. Establish Python 3.12, dependency locking and test/lint tools; select compatible LangGraph and SQLite checkpoint dependencies rather than copying an unrelated lockfile.

Implement inventory less allocations, time-phased material needs, weekday calendar, shared production capacity, usable receipt before production start, exact revision/approval, reservations, MOQ/multiples, fixed freight and cost-first ranking. Enumerate the small candidate space deterministically; independently check feasibility and quantity conservation. Preserve unknown cost, missing evidence and failed reads as distinct states.

**Gate:** initial evidence yields 300 required drivers, 180 usable, 120 uncovered; cumulative recovery requirements are 60 before Thursday's start and 120 before Friday's start. With released confirmations, derive the $1,220 plan (transfer 40, Express 20, Standard 60) and $1,480 Reserve fallback. Keep the original $2,400 PO obligation separate. Test altered values so these answers cannot be hardcoded.

## Phase 2 — Durable lifecycle and real multi-agent work

Implement one run registry, LangGraph state/checkpoints, immutable artifact store, operation receipts, exact human request/response bindings, cancellation and persisted usage/deadline. Add scripted providers first for reproducible offline acceptance, clearly labeled scripted.

Register three roles with different allowed tools and validated outputs. Run independent Operations/Sourcing discovery concurrently; join deterministically before Qualification. Reviewer findings create targeted work orders, evidence requests or candidate rejection. Implement exact-request-bound dated reply admission, wait/resume, partial failure and exhausted-budget escalation. Changed evidence invalidates selections/drafts; reuse prior work only when dependency fingerprints match.

Bound invocations to eight turns and one repair, at most two concurrent workers and three proposed-plan revisions, plus shared run-wide limits that survive restart and scenario-clock advancement. Completed paid-I/O receipts may be reused; interrupted calls with unknown completion cannot be blindly repeated.

**Gate:** the allocation ledger reduces warehouse stock from 100 on hand to 40 transferable; quotes without receipt/reservation fail qualification; the wrong revision is rejected. A later reply changes findings and plans. Restart during evidence wait and human wait preserves requests, state, budgets and deadline. No source response is fabricated.

## Phase 3 — Snowflake evidence, Search and hosted Claude

Prepare reviewable SQL/load assets for a dedicated versioned synthetic schema. Separate operational sources/documents/claims from evaluator-only future events/oracle labels. Build snapshot manifests and immutable source hashes, scoped bounded SQL, safe errors, query tags and timeouts. Keep backend credentials private and source/provider failures explicit.

Create scoped Cortex Search over permitted supplier/technical text; enforce server-controlled company/scope/version/time filters and revalidate hits against SQL snapshot records. Index only approved evidence, never this handoff or answer-bearing research documents. Verify Search service isolation deliberately because it uses owner rights.

Adapt the reference `AI_COMPLETE` tool-loop pattern for Claude with constrained output, Pydantic validation, registered tools and engine-result grounding. Pin a model supported by the actual account/region. Implement an idempotent local outbox exporting sanitized run analytics and frozen/evaluated results to Snowflake; do not move approval/checkpoint ownership there as a side effect.

**Gate:** mock-connector tests pass first. Run approved live SQL, Search, Claude and analytics/evaluation-write checks independently, each labeled by actual outcome. Verify correct cited document IDs, scope and hashes. No fixture substitution or provider switch follows a live error. Prepare concrete bounded checks and setup assets before requesting missing paid/cloud authorization.

## Phase 4 — API, console and human review

Keep `GET /api/health`. Translate raw Snowflake exceptions into safe error codes/messages instead of returning `str(e)`. Add lifecycle endpoints under `/api/graph/*` for starting/getting a run, events/evidence, exact evidence replies, human selections, refresh, cancellation, drafts and outcome-report retrieval. Define idempotency, exact checkpoint references and stale-state errors in typed contracts before UI wiring.

Expand the existing React connection page into a focused assessment flow: start scenario → shortage and evidence → candidate/qualification findings → recovery alternatives and trade-offs → human approve/choose alternative/defer → draft. Show real parallel role spans, sources, missing predicates, waits and failures. Keep connection/provider details secondary. Retain JavaScript unless a TypeScript migration is deliberately chosen.

Re-read authoritative evidence before accepting a decision and drafting. Bind selection to the precise current snapshot/policy/plan and human request. Generate deterministic transfer/purchase/evidence-request templates; all remain `DRAFT_NOT_SUBMITTED`.

**Gate:** browser inspection demonstrates evidence wait/resume, a supported plan, all three human decision paths and an altered-source stale-selection rejection. No draft appears from stale approval or failed qualification. API/UI never exposes prompts, credentials or raw provider exceptions.

## Phase 5 — Freeze, replay and architecture comparison

Create immutable frozen records for the recommended plan and any different human-selected plan before held-out events are visible. Replay hidden realized receipts under declared simulator assumptions. Report on-time units, whole orders, remaining shortage, cost, forecast error and paired change versus no recovery; do not report forecast as realized savings or claim guaranteed recovery.

Keep oracle answers permanently evaluator-only. Release future supplier documents at their event times through the same evidence admission path, without fixture answer labels. Separate an extraction test from a trusted pre-normalized-feed test. Preserve the standalone copied evaluator as a simulation reference, not model-quality evidence.

Compare deterministic pipeline, same-tool single agent, three sequential specialists and concurrent specialists under matched evidence release, budgets, objectives and cache conditions. Allow the single agent parallel tool calls. Report measured quality, latency, calls and cost/unknown usage; do not assume multi-agent superiority. Introduce family-held-out cases before claiming generalization.

**Gate:** tests prove no future/oracle retrieval, frozen-plan immutability, correct alternate-plan attribution and reproducible replay/scoring. Stress-grid coverage is labeled sensitivity, not a real-world probability.

## Parallel work ownership

After Phase 1 contracts stabilize, delegate deterministic engine/tests, Snowflake/inference adapters and UI/API work to separate builders. One integration owner owns workflow, persistence, schema versions and final acceptance. Agree on typed interfaces before parallel edits; do not let agents create independent run hosts or compete over the same files. Phase 5 can be developed independently once frozen-plan contracts are stable.

## Final verification and delivery

Add focused tests under `backend/tests/`, a locked/reproducible backend environment, a local launcher and demo fixtures. Document exact commands for `python -m pytest`, `python -m ruff check`, `python -m ruff format --check` from the backend environment, and `npm ci`/`npm run build` from `frontend/`. Use a new isolated storage directory for acceptance and exercise real API process restart. Add a frontend test command only when implementing meaningful UI checks; none exists in the scaffold today.

Deliver an offline scripted demo, working approved Snowflake/Claude route, setup/load/verify assets, Mermaid graph, runbook and sanitized acceptance matrix. Distinguish local/scripted checks, mocked connectors, live successes, failures and not-run services. No paid service was invoked during preparation of this package. Native managed Cortex Agents, Analyst/semantic views, MCP, broader live catalogs and production deployment follow only after the core release and their own acceptance evidence.
