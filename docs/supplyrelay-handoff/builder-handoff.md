# Build SupplyRelay in this repository

Work in `/Users/nikms/Documents/cmu/hackathon/actionable-smart-supply`. Build **SupplyRelay**, a Snowflake-grounded, Claude-powered multi-agent supply-chain recovery application. Follow [implementation-plan.md](implementation-plan.md) and the [original detailed handoff](reference/linekeeper/docs/snowflake-claude-multi-agent-builder-handoff.md), applying the destination adaptations below.

## Destination adaptations

The existing repository is a small FastAPI backend in `backend/` and React 18/Vite JavaScript frontend in `frontend/`. It currently contains liveness, Snowflake connection diagnostics and a connection-status page. It has no product workflow, durable run host, deterministic recovery engine, agent roles or tests. Do not report the copied Linekeeper excerpts as implemented SupplyRelay features.

Create the new modules under this repository's existing structure. Retain the current frontend stack unless a migration is separately justified. Adapt the reference patterns deliberately; do not import `linekeeper` from the reference directory, copy the old application wholesale or make the new product depend on its original checkout. Preserve existing destination work and its liveness endpoint. Replace raw provider-error exposure with safe diagnostics as part of implementation.

Linekeeper-specific instructions to preserve its old profiles, archives, launch modes and storage pins apply to that source repository, not to imaginary stores in this scaffold. Do not modify the Linekeeper checkout during this build. In SupplyRelay, establish explicit schema/policy/graph versions and preserve history from the first working version onward.

## Required product

Supplier delivery change → validated dated snapshot → Operations and Sourcing investigations → Qualification → deterministic feasible recovery plans → recommendation and alternatives → human approve/alternative/defer → evidence/deadline/authority recheck → deterministic review-only drafts.

Implement exactly the three substantive specialist roles from the consolidated design:

- **Operations investigator:** reconcile inventory and allocation records, BOM, production/orders, shared capacity and inbound commitments; use deterministic tools to establish time-phased shortage and affected orders.
- **Sourcing investigator:** investigate exact part/revision, internal transfers and external offers; distinguish quote, reservation and usable receipt; prepare specific evidence requests and grounded candidates.
- **Qualification reviewer:** independently validate evidence predicates and return supported, rejected or unresolved findings with precise reasons and targeted work orders. Qualification is not managerial approval.

Operations and independent Sourcing discovery can run concurrently, with a default maximum of two workers. Quantity-dependent work waits for validated Operations output. Use a single deterministic merge, immutable typed artifacts and a meaningful reviewer feedback loop. Scheduling, arithmetic, optimization and draft construction remain deterministic. Model narration may explain verified plans but cannot change quantities, feasibility or policy ranking.

Use one LangGraph application lifecycle and SQLite-backed run registry/checkpoints, exact human bindings, durable deadlines/budgets and operation receipts. The selected Linekeeper runtime excerpts demonstrate patterns to adapt; they are not an already installed dependency. Never create competing run/approval writers.

## Snowflake is a core platform

Required integrations are scoped operational SQL, versioned documents/claims, Cortex Search, Claude hosted through Cortex `AI_COMPLETE`, an idempotent audit export and separate outcome/evaluation storage. Model access, SQL, Search and audit writes must be tested and reported independently. Keep the existing connector/configuration as the starting seam, subject to safe errors, timeouts, row bounds and scope validation.

Choose an account-verified Claude model and schema-constrained tool outputs; enforce Pydantic contracts, role tool allowlists and deterministic factual validation. No silent provider/fixture fallback. Distinguish application multi-agent orchestration from native managed Cortex Agent resources. Native Agents, semantic views/Analyst and MCP are later extensions, not prerequisites or unused configuration flags.

Use a dedicated versioned synthetic schema. Search results must resolve to permitted SQL snapshot records with matching source IDs, scope, hashes and availability times. Search owner-rights behavior requires deliberate service isolation and server-controlled filters, not an assumption that caller table permissions protect indexed rows. Official references are linked in the original handoff.

## Evidence, decision and evaluation boundaries

Start from the [initial case](reference/linekeeper/docs/research/shortage-recovery-v1/initial_case.json), not the answer-bearing research prose. Admit supplier replies through a typed exact-request/run/snapshot-bound input path. Human clarification is evidence, not automatically authoritative quantity, price, policy or availability. Missing evidence causes a persisted request/wait, abstention or another fully supported option.

Keep scenario time separate from real authorization/deadline time. Event release does not replenish budgets. At most eight turns per role invocation, one repair and three plan revisions, all inside an explicit shared run-wide budget. Keep unknown costs and unknown usage null/unknown. Changed evidence invalidates prior selection and drafts; require current reselection.

Freeze the exact recommendation/actions, quantities, forecasts, policy/evaluator versions and input fingerprints before hidden outcome replay. If the human chooses an alternative, freeze that choice separately. Future event documents may be released at their scenario times; oracle labels remain evaluator-only permanently. Never leak fixture `validated_fields` into an extraction benchmark. Report assumption-based stress coverage as sensitivity, not empirical supplier reliability.

Keep the human selection boundary. Every generated purchase, transfer or evidence-request artifact is `DRAFT_NOT_SUBMITTED`. No email, ordering, inventory movement or customer-commitment tool is authorized.

## Execution and finish criteria

Use subagents for independent engine, Snowflake and interface work after agreeing on contracts; one owner integrates workflow, persistence and versioning. Complete the authorized local implementation and checks. Use existing explicit authorization for live work; if missing, prepare the concrete setup/checks and bounded paid-call budget before requesting it. Never put secrets in chat, source, SQL, reports or frontend code.

Deliver a working offline demo, configured live route, reproducible Snowflake setup/load assets, tests, updated runbook and Mermaid workflow, and a sanitized acceptance report. Distinguish scripted/local, live verified, failed and not-run checks. The implementation plan gives milestone-specific acceptance gates; a populated configuration is not proof of authenticated service behavior.
