# Final-email NorthStar v3 workflow

NorthStar is now a versioned scenario in **one Linekeeper application**. All graph modes use
`GraphApplication`, shared runtime/checkpoints/history and `/api/graph/*`. The former independent
NorthStar composition has been retired. Read [the integration report](shared-scenario-integration.md)
for current ownership, compatibility and verification; the earlier UI-only integration report is historical.

## One-command local demonstration

From the repository, with uv, Python 3.12 and Node >=18.18:

```bash
BACKEND_PORT=8012 ./scripts/dev.sh graph-demo
```

Open <http://127.0.0.1:8012/>. Choose **NorthStar Robotics · Customer recovery**, then click **Start assessment**, inspect **At risk & evidence** and the two
options in **Decide**, select **A · Apex emergency purchase**, then **Approve A · Prepare draft PO**.
The draft opens under **Proposals**. **Steps & log** holds recorded execution and technical details. B and defer require a reason and produce no
Apex PO. All approvals use an explicitly simulated loopback manager identity. SQL, Search
and model badges say MOCKED in this mode. The calculations and HTTP lifecycle execute.
The question is intentionally locked to the reviewed demo scope; arbitrary questions fail closed.

`graph-demo` and the `northstar-demo` compatibility preset expose the same scenario catalog.
`graph-live` retains its original model adapters; `legacy` remains unrelated. No credentials or external
model calls are required for either demo mode. The launcher explicitly selects Python 3.12.

## Architecture and supported boundaries

`src/linekeeper/northstar_v3/` supplies the versioned business contracts and adapters:

- `models.py`: strict source, shared policy, ShortageCase, DecisionPacket and option contracts.
- `sources.py`: original flat-pack validation; dedicated-schema Snowflake SQL; Cortex Search;
  explicit SQL document fallback. Seven table families are read in one bounded SQL statement.
- `engine.py`: inventory coverage, overdue POs, source precedence, shared-capacity scheduling,
  recovery evaluation, contract-first ranking, advisory economics and slip tolerance.
- `agents.py`: exactly two sequential business roles, registered bounded tools and validated
  submission adapters. Agent 2 only starts after Agent 1's submission passes validation.
- `application.py`: deprecated client adapter into `GraphApplication`; it owns no lifecycle.
  `product_workflow/final_profile.py` supplies business steps and the PO template.
  `GraphApplication` and `ProductGraph` own durable requests, checkpoints and approvals. A writer lock enforces one process per storage directory. Budgets and a real-time
  one-hour deadline survive refresh/restart; refresh invalidates the previous decision/draft.
- `cloud.py`: reproducible setup and source checks; `acceptance.py`: real API/restart harness.

Both roles receive the exact same Pydantic-validated policy. Unsupported semantic policy changes
are rejected rather than silently ignored. The scenario date (October 26, 2026) is separate from
real observation/authentication times. Synthetic local authorization expires January 1, 2027 UTC.

Documents are evidence, never policy or authorization. For the core three-document pack, a
strict source-specific extractor grounds supplier quantity, entity, dates, qualification, quote,
price, total and terms before calculation. Unsupported prose fails closed. This is a bounded
synthetic document adapter, not general supplier-email understanding. Missing confirmation never
becomes emergency availability. Missing or unknown costs remain null.

Agent final submissions must match the typed engine result in full, including every numeric
field and citation. One correction is allowed; each role has at most eight model/tool turns.
There are separate durable counts for scripted turns, paid model attempts, tool calls,
evaluations and refreshes. There is no model-response cache. This mode bounds call counts, not monetary/token usage;
legacy GRAPH_TOKEN_CAP/GRAPH_COST_CAP settings are rejected rather than silently ignored. A rejected/failed agent can expose
an explicitly labeled engine-only result, but cannot reach approval through that failed run.
Actual events contain safe metadata; immutable source bodies are available through the
run-scoped evidence links, separately from diagnostics.

Safety stock is warn-only; demand is not counted twice. The scheduler shares 60 starts/day
across plans, allocates contracted orders by promise/ID before forecast, and computes shipment
risk against the customer's promise. Partial planning allocation is not shipment consent.
The application supports this single-model, single-constraining-part slice. Other material
shortages, multiple recoveries and multiple products require an explicit extension.

The policy's economic tie-breaker conflict stays visible. Equal protection produces an unresolved
human-review tie. It does not silently rank by price. The $164,800 Pacific commitment stays separate
from Apex's $194,400 cash and $29,600 premium. No cost recovery entitlement is assumed.

For the authenticated hackathon schema provisioned in this task, the live launch is:

```bash
BACKEND_PORT=8012 ./scripts/northstar-live.sh
```

This wrapper supplies the tested non-secret object/model names and enables the live route.
Starting an assessment uses paid Snowflake SQL, Search and hosted Claude. Environment overrides
are honored; secrets remain in the approved local connection store. It never switches to demo.

## Cloud preparation and connection

A Cortex Code desktop sign-in and a Python connector sign-in are separate. The app never
extracts desktop tokens, changes credentials, alters account-wide settings or adds grants.
Configure an approved named connection locally using Snowflake's supported connector setup.
Keep secrets in the local connection store/backend environment, never in commands or this repo.
An `externalbrowser` connection may open its own sign-in flow when Python connects.

Set these **non-secret** backend variables, replacing object names if necessary:

```bash
export SNOWFLAKE_CONNECTION_NAME=sfsehol-anthropic_snowflake_hackathon_fevwgp
export SNOWFLAKE_DATABASE=DEFAULT_DATABASE
export SNOWFLAKE_SCHEMA=NORTHSTAR_V3_REV2_20261023
export SNOWFLAKE_WAREHOUSE=DEFAULT_WH
export SNOWFLAKE_ROLE=ATTENDEE_ROLE
```

The database must already exist and belong to an authorized demo scope. The schema must start
with `NORTHSTAR_V3_`; the manifest binds it to NorthStar and the policy/version. Runtime credentials
need read access and permission to query Search and the selected model. Provisioning privileges
are separate. Do not grant account-wide access to work around a failure.

First inspect the account and existing resources:

```bash
uv run --python 3.12 python -m linekeeper.northstar_v3.cloud preflight
```

This verifies authenticated SQL and inspects matching schemas/warehouses. In a worksheet,
`SHOW CORTEX BASE MODELS LIKE '%CLAUDE%';` lists account model lifecycle/region availability.
A listed model is not evidence of a successful inference call.

Prepare a concrete, reviewable setup file **before** cloud writes. Use a new output directory:

```bash
uv run --python 3.12 python -m linekeeper.northstar_v3.cloud prepare \
  --directory artifacts/northstar-v3-setup-new \
  --database DEFAULT_DATABASE --schema NORTHSTAR_V3_REV2_20261023 \
  --warehouse DEFAULT_WH
```

The generated SQL contains only fictional pack data. The inspected DEFAULT_WH is X-Small with
60-second auto-suspend and is reused without alteration. To request a new dedicated warehouse,
set SNOWFLAKE_WAREHOUSE and --warehouse to its new name. `--create-warehouse` creates a new X-Small
warehouse with 60-second auto-suspend. Omit it when the owner supplies an existing suitable
warehouse. No shared warehouse is altered. Setup uses CREATE, never replacement/deletion, and
fails if objects already exist. A partial failed import is preserved for owner inspection;
choose a new versioned schema or have the owner manage cleanup. Do not rerun against saved data.

After authentication/privileges are available, run the same setup through the named connection:

```bash
uv run --python 3.12 python -m linekeeper.northstar_v3.cloud provision
uv run --python 3.12 python -m linekeeper.northstar_v3.cloud verify
```

Verify checks all three IDs and document metadata/bodies through real Cortex Search after indexing.
It does not certify model execution. If Search is unavailable, explicitly set
`NORTHSTAR_SEARCH_FALLBACK=true` and use a **new storage directory**. Document SQL remains LIVE;
Search is labeled MOCKED. A Search failure never silently enables fallback.

## Live agents and launch

For Snowflake-hosted Claude, select a model ID actually supported by the account:

```bash
export NORTHSTAR_MODEL_ROUTE=snowflake
export NORTHSTAR_MODEL=claude-sonnet-4-5
export GRAPH_ALLOW_MODEL_EGRESS=true
export GRAPH_DIRECTORY=artifacts/northstar-v3-live-new
BACKEND_PORT=8012 ./scripts/dev.sh northstar-live
```

This is **application orchestration using AI_COMPLETE**, not native managed Cortex Agents.
The identifier above succeeded in the authenticated application release trials; the complete
Python role-loop results are recorded separately in the verification report. No cross-region/account setting
is changed by this application. Hosted response JSON is validated before any tool runs.

Alternatively set `NORTHSTAR_MODEL_ROUTE=anthropic`, `LLM_PROVIDER=anthropic`, an appropriate
`LLM_MODEL`, and configure `ANTHROPIC_API_KEY` privately in the backend. That route uses native
Claude Messages tool use and separate Anthropic authentication; a Snowflake token is not that key.
It never silently switches providers on failure. Both live routes read Snowflake, not fixtures.

Cortex Analyst, semantic views, managed Cortex Agents, MCP, concurrency and broad optimization
are deferred platform extensions. There are no ERP/email/stock-movement tools.

## Reproduce acceptance

```bash
uv run --python 3.12 pytest -q
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
cd web && npm run build
```

Original attachment files are excluded from repository formatting so they remain byte-for-byte
preserved. The reference pack checker is executed in an isolated normalized layout:

```bash
uv run --python 3.12 python -m linekeeper.northstar_v3.cloud reference \
  --directory /tmp/northstar-v3-reference-new
```

Expect **48** PASS lines. The older checker separately produces **62**. Neither tests live agents.
The 10 v3 agent cases run three times with scripted roles in `tests/test_northstar_v3.py`; additional
checks exercise altered inputs, source failures, grounding, limits, unknowns, stale approvals,
restart, policy ties and governance. Mocked connector tests do not certify SQL syntax or cloud access.

For three actual API processes/restarts ending in approval and draft:

```bash
uv run --python 3.12 python -m linekeeper.northstar_v3.acceptance \
  --directory artifacts/northstar-v3-acceptance-new
```

Use `--mode northstar-live` only after the intended named connection/model route is configured;
that explicitly executes paid SQL/Search/model work. The same numeric checks apply. It never
falls back to scripted responses. The sanitized report identifies the actual routes and counters.
The harness's approval is a simulated development-manager decision, never a business authorization.

## Three-minute demo

Use the supplied demo/Q&A files as source material, with these execution labels:

1. **0:00–0:30 · LIVE local UI.** Ask the fixed manager question; point out the scenario date.
2. **0:30–1:10 · LIVE engine, MOCKED agents/SQL/Search in northstar-demo.** ERP overdue detection
   precedes the supplier email. Show unchanged October 20 ERP ETA and November 10 planning ETA,
   380 uncovered drives, 60 contracted units and $1.08M gross exposure. Open DOC-SUP-001.
3. **1:10–2:10 · LIVE engine, MOCKED model/retrieval in demo.** Show confirmation-required A,
   REC-002 exclusion, A's zero risk versus B's 20/$360K, cash/premium/Pacific obligations separately,
   November 4 slip tolerance and the $29,600/$1,480/$7,400/8.2% advisory break-even. Flag false.
4. **2:10–2:40 · LIVE local approval/template.** Approve A and inspect/download `DRAFT_NOT_SUBMITTED`.
   Show supplier deadline, authority, quote/confirmation and Pacific follow-up. Nothing is submitted.
5. **2:40–3:00 · LIVE trace.** Two sequential roles, one shared policy, deterministic checks,
   explicit human decision. Do not claim measured savings, global optimality or production auth.

The three authenticated release trials now verify the northstar-live SQL/Search/model segments
as LIVE. For a new run or account, use LIVE only when its actual trace confirms success. Desktop connection/model smoke checks alone do not change these labels.
