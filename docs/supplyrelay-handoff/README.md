# SupplyRelay implementation handoff

This folder is a selective reference package for building **SupplyRelay** in `/Users/nikms/Documents/cmu/hackathon/actionable-smart-supply`. It is documentation and referenced examples, not an installed application or a wholesale copy of Linekeeper.

## Reading order

1. [Builder handoff](builder-handoff.md) — current destination, scope and architecture decisions.
2. [Implementation plan](implementation-plan.md) — concrete phases, ownership and acceptance gates for this repository.
3. [Original detailed handoff](reference/linekeeper/docs/snowflake-claude-multi-agent-builder-handoff.md) — full role, evidence, budget, Snowflake and evaluation contracts.
4. [Scenario specification](reference/linekeeper/docs/research/shortage-recovery-v1/README.md) and [initial case](reference/linekeeper/docs/research/shortage-recovery-v1/initial_case.json).

The original handoff was written for an existing Linekeeper application. **The destination-specific handoff and plan take precedence for workspace paths, module locations and build sequence.** This repository currently has a FastAPI/React connection scaffold; it does not already contain Linekeeper's GraphHost, runtime or console.

## Package boundaries

- Only the original handoff, directly relevant cited documents, selected source/test examples, the complete small scenario/evaluation pack and these destination notes are included.
- `reference/linekeeper/` preserves original paths for comparison. Python imports and frontend imports in these excerpts are intentionally not dependency-complete; they are reference examples, not code to run in place.
- The source `AGENTS.md` is stored as `AGENTS.reference.md` so historical repository instructions do not become active instructions for this new repository.
- Scenario files keep their original sibling layout because the standalone evaluator depends on it. Its use of pre-normalized confirmation fields verifies simulation, not model extraction or live services.
- Future events, oracle labels and answer-bearing research prose are evaluator/developer material. They must never be indexed or served wholesale to planning agents. The builder must enforce separate planner/evaluator access.
- Credentials, `.env` files, databases, runtime artifacts, dependency installations, built frontend output, unrelated research and the rest of the old application are excluded.
- No live SQL, Search, model, supplier or other external action was executed while preparing this package.

The file inventory below records source-relative paths and SHA-256 values for the exact reference copies. New destination notes are authored for this package.

## Exact source inventory

| Source path in Linekeeper | SHA-256 |
| --- | --- |
| `AGENTS.md` | `68d484280bb68b1add2bb6e498adf1ce7102a2f4492b2e3ec5ff7a7f64db531f` |
| `docs/snowflake-claude-multi-agent-builder-handoff.md` | `772809630f0bfafa452aa062bbf14f36c67bfd1a474dffb1aec99e37764d0057` |
| `docs/shared-scenario-integration.md` | `43fd20514da957dceeded410311ce689dd6f482fd5a0c95403553d5fc98ef9a6` |
| `docs/northstar-v3-runbook.md` | `ec9a5b5d1e5996457c799a1bf7d9a36872c54937e79edf7df5f6fda76ecdea1b` |
| `docs/implementation-handoff.md` | `949e5a8726f14c45d281ff1cde6211e31adfedd7351522b41ec3c21e3cf40284` |
| `src/linekeeper/product_workflow/application.py` | `d4ae445f8eee59de77cb95b1e3e9adc839a332ee8c1192b1ff264d63aa2f1fc6` |
| `src/linekeeper/product_workflow/product_graph.py` | `aea4464d90cbbcb5525658ea9702d3855bb0c6db3c0041f99fd6019eb4d1a106` |
| `src/linekeeper/product_workflow/final_profile.py` | `abce55e2df951d439db2eeedf1b61ebecee531a24bf213651276aae8aeabac46` |
| `src/linekeeper/agent_runtime/graph.py` | `565ef9af8e2d1dec6978e69763d551b25102470f634194033c19c01b94f9c194` |
| `src/linekeeper/agent_runtime/host.py` | `638005b1d19709bf3232767f7e4c14f36da81a1ee4aceedae149aa0c0c1bba67` |
| `src/linekeeper/agent_runtime/execution.py` | `3a3f8686797217c006e7646811bc9177961bd1a472628325267a35c5532d0afb` |
| `src/linekeeper/agent_runtime/storage.py` | `12554fe7f859877973f3193073a414459f4f964c44addad3c20f4d2d11e2b551` |
| `src/linekeeper/data/scenario_source.py` | `e9bdc83f5533dd54689af17e176211b4ee0ad7b3584c946cd74e86d10d2634ea` |
| `src/linekeeper/northstar_v3/sources.py` | `359b60178d636f9ba3074f61ac19070cdd975e7d0653000c8eafca96f2b2c9c8` |
| `src/linekeeper/northstar_v3/agents.py` | `d65144586b3416f241dc38129ee431bb259c791ef2ad4f531481a552be15956a` |
| `web/src/components/Activity.tsx` | `671e6c8e563f62fda1a029a5fd6d191f3e89a14dfa5cc17f7a464262393d2294` |
| `scripts/dev.sh` | `2e0e2ef47578f9dcad17b2f65cc1ac606dcd5d4ad7cbab02843a431ad315c9ac` |
| `tests/test_shared_scenarios.py` | `3a4d66995b53084ae259b4f7fb344c47dd1e883e5d7352fcbe40d0c0a227efc7` |
| `tests/test_northstar_v3.py` | `1d5d288bea3eeebd86bf48b9e9ec08a126318e6dd38739e44971a2f62abc03ba` |
| `docs/research/shortage-recovery-v1/README.md` | `923475fca9b0740ef84a12f9c5d37f66dfa6f234b6fadbed5e8e82dd256a494b` |
| `docs/research/shortage-recovery-v1/evaluate_outcomes.py` | `d324236a4f22d93b12d5cd429d92185b4659419a2e7faae6c4d6b189b9f3fac1` |
| `docs/research/shortage-recovery-v1/evaluation_oracle.json` | `5a5f6651dd860b306f6fe9fab51798a0ee4de9725792ffcb9571c08c56367c1b` |
| `docs/research/shortage-recovery-v1/future_events.json` | `d4f50449208c07a5c07b3c86fa8307e3b6fec765bb858107bd0f78cc03f8ffe9` |
| `docs/research/shortage-recovery-v1/initial_case.json` | `64ec3a0d1bacdb750ba4a79e6c8aaa205a16894d321f61a27621c056ecdc588d` |
| `docs/research/shortage-recovery-v1/outcome_evaluation.json` | `783aab5897e3c1fb2fe20464476bcab6471a14ce607dfc5bcf76d042f38c155f` |
| `docs/research/shortage-recovery-v1/public-outcome-validation.md` | `21ae08bd06108e7bcbf7e6737f162f90c941da9320d15f395dc2e5894a3d1c2e` |
| `docs/research/shortage-recovery-v1/recommendation-and-outcomes.md` | `648388ee707b725837057ce9b9ab0bcc09075341743b08eae58d74055cc5583e` |
