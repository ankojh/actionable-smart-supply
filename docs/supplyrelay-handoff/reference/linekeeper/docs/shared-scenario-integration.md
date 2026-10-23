# Snowflake-grounded Linekeeper — October 23, 2026

## Integration and ownership

Linekeeper now constructs **GraphApplication for every graph mode**. NorthStar Robotics is a
versioned company/scenario profile in that application. The `northstar-demo` and `northstar-live`
launch names are compatibility configuration presets, not separate applications. `legacy` is
still the unrelated older RFQ API.

The gap was deeper than navigation: `NorthstarApplication` formerly owned a second executor,
`workflow.sqlite` run registry, hand-written checkpoint UUIDs, refresh loop, decision handler and
proposal persistence. Those responsibilities now belong to `GraphApplication`, `ProductGraph`,
`GraphHost` and `SQLiteRuntimeStore`. The old Python class and `/api/northstar/*` routes translate
old clients into that same application; they have no independent orchestration or database writer.
The console uses `/api/graph/*` and `#/runs/<id>` for both profiles. Old hash links remain readable.

New runs share `application.sqlite` (`app_runs`), `runtime.sqlite`, `checkpoints.sqlite`, run
contexts, exact HumanRequest/HumanResponse references, host checkpoint validation, cancellation,
deadlines, durable budgets and operation receipts. NorthStar's roles are registered as
`context.supply_risk` and `decision.recovery`. Role tool loops retain finite typed tool schemas,
eight-turn limits, one repair and full engine-result submission validation; model/tool results
and usage are recorded in the shared ledger. Interrupted I/O cannot silently replay paid work.

`data/scenario_source.py` binds a company, scope, source origin and authorization to losslessly
normalized `ScenarioEvidence`. The local final pack and the dedicated-schema Snowflake SQL adapter
use this same input contract. SQL manifest/company/policy validation precedes admission of evidence.
Missing values/read failures stop the assessment; a successful empty document read is recorded as
`empty`, and the engine retains the resulting missing evidence rather than substituting fixtures.
Search remains a separate bounded, SQL-grounded adapter; inference remains a separate provider.
Nothing connects through the legacy `LK_SNAPSHOTS` connector.

`ProductGraph` uses profile business steps for assessment and comparison, followed by its shared
human-wait/resume loop. The final-email profile reuses `northstar_v3/models.py`, `engine.py`,
`sources.py` and `agents.py`. Its typed evidence and evaluator remain versioned because coercing
these inputs into the earlier Context schema would lose shared production capacity, advisory
costs and warn-only safety-stock semantics. This is a business schema/evaluator extension, not
a second run host or another wrapper around independently executing applications.

Before consuming a human selection, the host's delivery hook re-reads the authoritative source
and recomputes the comparison in a receipt-backed operation. Changed evidence refuses selection
while preserving the human checkpoint for refresh. Refresh clears the current selection/draft,
keeps the earlier revision in audit history and requires reselection. Template output remains
`DRAFT_NOT_SUBMITTED`; B and defer produce no Apex PO. No business execution tools exist.

## Policies and UI

Final-email inventory is on-hand minus allocated; safety stock only warns. Supplier acceptance
is October 27. Contract protection decides A/B; economics are advisory, unknown transfer costs
remain null and Pacific's $164,800 obligation remains separate. The earlier `northstar` fixture
profile still deducts its version's safety stock and retains its earlier supplier conditions.
Neither dataset nor old evaluator was rewritten.

One landing page and history offer all scenarios. Both use the existing shell, risk/decision/
proposal tabs and canonical routes. Business-specific cards present shortage, customer impact,
recovery options and the next action. New final-email assessments use the shared activity/log
component; historical archives show their recorded earlier trace. Technical metadata is secondary.

```bash
# One credential-free application; choose NorthStar Robotics · Customer recovery or any older scenario.
BACKEND_PORT=8012 ./scripts/dev.sh graph-demo
```

`./scripts/dev.sh northstar-demo` is an older alias for the same composition. The existing
`scripts/northstar-live.sh` supplies the approved named Snowflake/model settings to this same
application; original scenarios in that preset still use their scripted adapters. Inspect each
assessment's recorded execution, not the global launch label. No cloud calls occur at startup.
`DATA_BACKEND=snowflake` now binds the final-email profile to its dedicated schema; it does not
reinterpret older fixture profiles. Live provider failure never selects scripted responses.

## Saved records and operating compatibility

No old database was migrated, deleted or rewritten, and no historical approval was copied into a
new assessment. Original graph runs remain in their original stores and retain their original
configuration pins and business schemas. Opening that directory with its original provider settings
adds the final-email profile through a separate profile pin, without changing existing run contexts.

An existing `<GRAPH_DIRECTORY>/northstar-email-v3-rev2.1/workflow.sqlite` is opened read-only.
Its original IDs, payloads, snapshots, decision references, history, traces and drafts are projected
into canonical history and assessment/evidence views. It cannot continue or refresh. Start a new
assessment for a current human decision. The archive is read once at application startup: stop the
old writer before switching the instance. This reader is the only remaining reliance on the former
NorthStar storage format. Old archive traces do not have newly fabricated runtime events.

At port 8016 the idle prior writer was stopped, its complete directory was backed up at
`artifacts/shared-integration-stopped-backup-20261023`, and Linekeeper was restarted against
`artifacts/northstar-v3-live-release-acceptance`. All four original payloads, including usage and
trace, match the pre-change API read; the old `workflow.sqlite` is byte-identical to the stopped
backup. None was approved/refreshed during this task. Older graph stores elsewhere on disk are
preserved; history is scoped to one configured directory, not federated across unrelated stores.

## Git and verification

Work stays on `dev/snowflake-v1`. A fresh fetch found HEAD `6ccdb34`, one ahead and zero behind
`origin/main` (`ea96065`). No rebase, reset, commit or push was needed. Existing changed/untracked
files were backed up under `/tmp/linekeeper-integration-baseline-20261023` before editing and were
preserved. No account credentials were placed in source, screenshots or this report.

- Full Python suite: **566 passed** in the final run (116.99 seconds), including actual loopback API/process
  restart acceptance after the audit projection correction.
  The first restricted attempt passed 556 but could not bind its process-restart socket;
  rerunning with loopback access passed.
- Existing NorthStar tests: **53 passed** unchanged. **9 new integration tests** cover modified
  Snowflake output reaching shared engine input, SQL/Search separation, company/provenance and
  authorization, successful empty versus missing/failed reads, both profiles in one registry/API,
  restart/human actions, stale checkpoints, refreshed history/draft invalidation and read-only
  archive byte preservation. Mocked connectors do not certify cloud SQL or connectivity.
- Three actual local API process/restart acceptance runs reached all ten final-email checks and
  `DRAFT_NOT_SUBMITTED` with scripted roles. Evidence:
  `artifacts/shared-final-process-acceptance-20261023/acceptance.json`. These requests use the
  compatibility URL adapter into the shared host; the new boundary tests also use canonical URLs.
- Older source checker: **62/62**. Final pack's isolated checker: **48 PASS**, ALL CHECKS PASSED.
- Browser: final-email explicit selection/draft and refresh invalidation; original profile's
  clarification/selection/proposal; mixed history and shared activity; saved live drafts and
  execution labels opened read-only. See the final check notes below and screenshot.
- New actual SQL calls: **0**. New actual Search calls: **0**. New paid model requests: **0**.
  Saved live traces were inspected, which does not reverify connectivity. The new shared live
  composition has not received a fresh billable cloud acceptance run in this task.

The bounded single-product/one-constraining-part scheduler, synthetic loopback identity,
unsupported token/monetary caps and absence of managed Cortex Agent/Analyst orchestration remain
as documented limitations. Drafts never submit orders, send messages, move stock or change promises.

## Final check notes

The shared activity view omits unused roles for this profile. Business-step audit metadata has a
distinct event type so it cannot prematurely complete a role's running operation. Older trace
metadata remains readable without being counted as a new role pass. Recorded B/defer decisions
are marked review-only and do not claim that a purchase order exists. Targeted timeline/shared
scenario checks passed **26 tests** after these corrections. Lint/format checks passed for **235
Python files**, the final frontend production build passed, and the working diff is whitespace-clean.

The old four-record NorthStar database and all payloads (usage/trace included) remained identical
to the stopped backup and pre-change read. The saved-live browser check opened the draft and
recorded LIVE labels without starting, approving or refreshing any cloud assessment.

![One Linekeeper history and final-email scenario](images/linekeeper-shared-scenarios.png)

Final browser pass used the current code on isolated port 8017: A produced a $194,400 draft,
refresh cleared that draft and selection, B required a reason and produced no PO, and a fresh A
assessment produced the final screenshot. Original supplier-shortage clarification, selection and
information-request proposal were exercised in the same history. The shared log now shows two
actual role passes after one refresh (one per role per assessment round), and no unused coordinator
or reviewer rows. Final validation: **566 tests passed in 116.99 seconds**; after the separate source
diagnostic refinements, the unchanged NorthStar tests plus shared-boundary tests passed **62/62**.

The scripted screenshot assessment is `0a62ed5b-fef3-4daa-9b66-97ac283851ca`; its draft remains
`DRAFT_NOT_SUBMITTED`. The configured live app remains on port 8016 and the isolated scripted
review instance on port 8017. These are two verification instances of the same application.

Archived start-request retries also return their original read-only assessment ID instead of
creating new work. Both the old request ID and old run ID are reserved, preventing a retry from
starting paid SQL/Search/model execution or shadowing archived evidence. The final compatibility
regression checks cover this behavior in the unchanged archive database.

[Draft screenshot](images/linekeeper-shared-draft.png) shows the explicit `DRAFT_NOT_SUBMITTED`
label and purchase total. Neither screenshot contains credentials.
