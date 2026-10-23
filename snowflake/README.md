# Snowflake integration

> **Retired scenario integration.** The load JSON files described below have moved
> to `archive_data/pre_northstar_happy_path/snowflake/load/`. Do not load those files
> or treat existing cloud snapshots as the current happy path. The new local pack
> is [NorthStar V2](../data/northstar_happy_path/README.md); its dictionary and flat
> exports are prepared for a later migration. No cloud migration or deletion was
> performed. The remaining instructions document the previous implementation.

## Managed Cortex Agents

New live specialist runs default to four versioned managed objects in the existing application schema: `SUMO_OPERATIONS_V1`, `SUMO_SOURCING_V1`, `SUMO_QUALIFICATION_V1`, and `SUMO_SINGLE_V1`. **Cloud provisioning is pending explicit approval:** automatic approval review blocked creating these objects and granting their access. The existing running process and historical cases have not been converted or represented as native Agent executions.

From `backend/`, render the exact proposed DDL without network calls:

```sh
.venv/bin/python ../snowflake/load/agents.py
# After approval of these exact objects and grants:
.venv/bin/python ../snowflake/load/agents.py --apply
```

The setup identity needs CREATE AGENT on the dedicated schema. The script creates only these objects with `IF NOT EXISTS` and grants their USAGE and MONITOR to the existing `PROJECT_RECOVERY_APP_V1` role. It does not grant that role object-creation or evaluator privileges. Agent specifications use the configured Claude model, an 8,000-token/60-second orchestration budget per request, and only the role's read-only generic tools. No additional Search service is created.

The Agent Run REST API orchestrates tool requests; callbacks validate their names and input and bind them to the assessment's exact authoritative snapshot. Material balance, independent qualification and recovery planning remain deterministic. Search callbacks use the existing scoped Cortex Search adapter and revalidate SQL citations. A native failure does not fall back to direct completion. `SNOWFLAKE_USE_CORTEX_AGENTS=false` selects the legacy provider explicitly.

Each invocation journals thread creation, paid requests and tool receipts before I/O. Completed receipts survive restart; uncertain calls are not repeated. Findings record the managed object, 64-bit thread/message IDs as strings, request/run IDs and available usage. Analysis and activity display Agent page and Observability-history links with identifiers for locating the exact execution. Old direct-completion cases retain their original provenance.

After provisioning, restart the backend and create a fresh live assessment, then use Refresh evidence to admit the already-published confirmed snapshot. Verify the resulting native execution records in Snowflake before claiming live acceptance. This integration currently has mocked coverage; native live execution remains blocked on the approval above.

Official references: [Agent objects](https://docs.snowflake.com/en/sql-reference/sql/create-agent), [Run API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run), [Threads API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-threads-rest-api), and [Observability](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-monitor).

The backend uses immutable Snowflake source rows, governed operational views and snapshot manifests. It validates payload/document hashes before planning. A live failure stays a live failure. Local fixtures are used only by the separately labeled offline provider and explicit synthetic source loader.

Run these from `backend/` with its virtual environment. Credentials belong only in `.env`; commands below never print them. The existing `SNOWFLAKE_DATABASE` and `SNOWFLAKE_WAREHOUSE` select the destination. The dedicated schema defaults to `SUPPLY_RECOVERY_V1`. The application uses `PROJECT_RECOVERY_APP_V1` regardless of a broader setup role. Configure `SNOWFLAKE_CORTEX_MODEL` to an account-supported Claude model; the default `claude-sonnet-4-6` is a candidate, not proof of account availability. `SNOWFLAKE_REST_BASE_URL` can explicitly select the official HTTPS account URL.

Prepare and review without cloud calls:

```sh
python ../snowflake/load/setup.py > /private/tmp/project-snowflake-setup.sql
python ../snowflake/load/load.py
python ../snowflake/verify/check.py sql
```

Setup requires an identity allowed to create the dedicated schemas/roles and grant privileges. Default rendering and setup omit Search because creating a service starts billable indexing. Existing objects are preserved with `IF NOT EXISTS`; schema changes require a new versioned migration. Run the following only with cloud authorization:

```sh
python ../snowflake/load/setup.py --apply --grant-user YOUR_SNOWFLAKE_USER
python ../snowflake/load/load.py --apply
python ../snowflake/load/setup.py --apply --include-search --grant-user YOUR_SNOWFLAKE_USER
```

The Search owner must be granted to the setup operator so the script can use it. `--grant-user` grants the dedicated owner and application roles only. Grant `PROJECT_RECOVERY_EVALUATOR_V1` independently to an evaluation identity; the application role receives no privileges on the `_EVAL` schema. Avoid assigning evaluator privileges to the application identity. Use an application-role-bound PAT for Search/Analyst REST calls. SQL supports the scaffold's PAT, password, keypair and browser authentication; `SNOWFLAKE_REST_AUTH=session` uses the configured SQL login with an application-role session token; `pat` uses the explicit REST PAT. Neither route falls back to the other. A role-bound PAT cannot be used to export evaluation results through another role; use a separately configured evaluation process/identity for that check.

The versioned source tables are `INVENTORY`, `ALLOCATIONS`, `BOM`, `PRODUCTION_CAPACITY`, `CUSTOMER_ORDERS`, `INBOUND_POS`, `RECOVERY_STOCK`, `SUPPLIER_OFFERS`, `RESERVATIONS` and `POLICIES`. Every row binds company, scope, schema version, snapshot, availability and content hash. The transactional loader publishes the manifest last. `EVIDENCE_DOCUMENTS`, `EXTRACTED_CLAIMS` and `SNAPSHOT_MEMBERS` bind each citation to exact text and hash. The application cannot insert authoritative documents, claims, source rows or manifests.

Release source confirmations explicitly after an evidence request:

```sh
python ../snowflake/load/load.py --release-as-of 2026-11-02T12:00:00
python ../snowflake/load/load.py --release-as-of 2026-11-02T12:00:00 --apply
```

This creates a new immutable snapshot and admits only supplier documents released by that scenario time. The initial load never opens the withheld confirmation file. The source package contains verified normalized fields and is a trusted-feed test, not a claim-extraction benchmark. Application reply admission requires exact text/hash/claims/source reference against this prepublished successor. Human clarifications do not rewrite operational truth. `source_reference` is the source package's `synthetic-supplier-feed-v1/EV-*` value.

Search indexes only published approved source documents from the dedicated synthetic company/scope. The service owner has no evaluator access. Backend Search always applies company, scope, schema, snapshot and time filters, then re-reads SQL snapshot membership and revalidates every returned hash. These checks are essential because Search uses owner rights. `SEARCH_PREVIEW` is reserved for diagnostics, not application serving.

`RECOVERY_ANALYTICS` is a focused semantic view over production demand, with snapshot/company/scope/version dimensions and robot/driver/order metrics. Analyst sends the exact scope and scenario time in the question. Returned SQL is displayed as a read-only review artifact and is never automatically executed. Models cannot turn Analyst text or SQL into approval-critical arithmetic; deterministic snapshot tools remain authoritative.

Each check runs independently and performs only its named surface:

```sh
python ../snowflake/verify/check.py diagnostic --live
python ../snowflake/verify/check.py sql --live
python ../snowflake/verify/check.py search --live
python ../snowflake/verify/check.py claude --live
python ../snowflake/verify/check.py analyst --live
python ../snowflake/verify/check.py persistence --live
```

The Claude check uses one 128-output-token call. The Analyst check uses one request. Search uses one query; the service may take time to index a new snapshot and empty results fail explicitly. Persistence writes one synthetic event and repeats that identical event ID. The application owns one durable outbox writer: standard Snowflake table constraints are informational, so separate concurrent writers must not share the same audit event-ID namespace. Audit payloads must contain only sanitized domain artifacts. Evaluation persistence requires its separate identity and is invoked with `evaluation-persistence --live`. Hidden realized events/oracle labels remain absent from planner tables and Search permanently.

Official API references checked during implementation: [AI_COMPLETE single-string and response metadata](https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string), [structured output schemas](https://docs.snowflake.com/en/sql-reference/functions/ai_complete-structured-outputs), [Search REST and filters](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/query-cortex-search-service), [Cortex Analyst REST](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/rest-api), [semantic-view DDL](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view). API review and mock tests are distinct from account authentication and live service verification.

## Search lifecycle and final session status

The implementation session verified SQL, scoped Search, hosted Claude specialists, and audit persistence. Analyst's last live request failed application SQL review; the parser correction is tested locally but has not been rechecked live. Separate evaluator credentials were unavailable. See [the acceptance report](../docs/acceptance/report.md) for evidence and limits.

Both Search indexing and serving were suspended after the approved checks. An authorized operator can use the dedicated owner role with the following commands; resuming starts ongoing service charges:

```sql
USE ROLE PROJECT_RECOVERY_OWNER_V1;
ALTER CORTEX SEARCH SERVICE DEFAULT_DATABASE.SUPPLY_RECOVERY_V1.EVIDENCE_SEARCH RESUME;
DESCRIBE CORTEX SEARCH SERVICE DEFAULT_DATABASE.SUPPLY_RECOVERY_V1.EVIDENCE_SEARCH;
-- Suspend both indexing and serving after the approved work:
ALTER CORTEX SEARCH SERVICE DEFAULT_DATABASE.SUPPLY_RECOVERY_V1.EVIDENCE_SEARCH SUSPEND;
```

Wait for serving to be running and confirm new evidence is indexed before a live assessment. Workflow preflight rejects a paused service before spending model calls. These commands apply only to this dedicated synthetic service; use the configured database/schema in another environment.

## Evaluator-only source publication

The planner never opens evaluator data. From `backend/`, `python ../snowflake/load/evaluation.py` validates the four separate synthetic realized events with no cloud calls. Its explicit `--apply` requires `SNOWFLAKE_EVALUATOR_TOKEN` and the evaluator role's INSERT grant on `_EVAL.REALIZED_EVENTS`. The setup assets include that grant; it was added after this session's provisioning and was not applied live. Application and Search-owner roles receive no evaluator-schema access. Live scoring requires this separate publication and identity; it never substitutes local outcomes after a cloud failure.
