# sumo

A supply-chain recovery workspace for the synthetic **NorthStar Robotics** case
`NS-2026-1042`. The active app reads cloud-published [NorthStar data](data/northstar_happy_path/README.md)
and evaluates dated evidence, full-BOM production constraints, supplier options,
manager decisions, and review-only action drafts.

## Run locally

Use Python 3.12 and Node.js 22.12 or newer:

```sh
uv venv --python 3.12 backend/.venv
uv pip sync --python backend/.venv/bin/python backend/requirements.txt
npm --prefix frontend ci
bash scripts/dev.sh
```

Open [the workspace](http://127.0.0.1:5173). The API runs on
[localhost:8000](http://127.0.0.1:8000/docs). The default provider is **live Snowflake**. Preserve the existing private
`backend/.env`; the backend uses its credentials for SQL and hosted Claude.
For a new account, validate and apply the [NorthStar ingestion](docs/northstar-integration.md)
before opening the app. There is no local fallback after a cloud failure.

## NorthStar workflow

The restored Risk Inbox opens the active V2 case with its complete, dated
NorthStar evidence. The workflow keeps the original Analysis, Investigate,
Compare, Decision and Action screens, with Company Brain alongside the case.
Risk Detection and Recovery Plan run as separate Snowflake Cortex Agents under
the native coordinator; Company Brain uses the same team and cloud evidence. Future scripted approvals
and execution records remain outside planning evidence.

The engine derives 500 required drives, 120 usable warehouse drives, and a
380-drive full-plan shortage. Contracts need 120 additional drives. Compare:

| Option | Contracted robots on time | Immediate expenditure | Forecast robots unbuilt |
| --- | ---: | ---: | ---: |
| No recovery | 60 / 120 | — | 130 |
| A: Apex 400 | 120 / 120 | $194,400 | 0 |
| B: pilot 80, conditional | 100 / 120 | Unknown until handling evidence | 130 |
| C: pilot 80 + Supplier C 40 | 120 / 120 | $24,000 | 130 |

Option C becomes eligible only after qualification, written supply confirmation,
MOQ, reservation, pilot-release, handling-cost and production checks. It commits
$170,400 less upfront than A; this is not net economic savings. The original
Pacific obligation stays open at $164,800. C leaves a 260-drive forecast gap.

Select a qualified plan and enter a reviewer name and rationale. Approval is
bound to the evidence fingerprint and scenario version, checked against the
$250,000 authority limit, and persisted with generated, unsent drafts. The app
never treats a scripted fixture approval as the user's consent. Option C's
October 29 simulated execution feed requires an explicit release after approval;
its events do not transmit drafts or represent actual customer shipments.

Live evidence, case state, decisions, findings and audit records persist in
`DEFAULT_DATABASE.NORTHSTAR_RECOVERY_V2`. It contains 205 source records across
25 typed tables and five published planning snapshots. This is a synthetic
business case with real cloud reads and model inference. Scenario dates remain
the dates of the source pack.

`PROJECT_NORTHSTAR_MODE=offline` explicitly selects the local provider for tests;
it is not the default and has no live-model fallback. The local provider stores
checkpoints under `backend/.runtime/` or the configured `PROJECT_STORE` path.

## Verify

```sh
cd backend
.venv/bin/python -m pytest -q
```

From the repository root:

```sh
npm --prefix frontend run build
python3 -m data.northstar_happy_path.validate
python3 -m unittest discover -s data/northstar_happy_path/tests -v
```

The backend suite covers NorthStar evidence gating, independently calculated
outcomes, infeasible supply mutations, concurrent/stale decisions, actual draft
generation, HTTP integration and real process restarts. Historical engine and
security regressions use exact test-only aliases to archived fixtures in
`backend/tests/conftest.py`; production does not fall back to the archive.

## Integration boundaries

The active frontend uses the `/api/northstar/*` case, investigation and chat
endpoints. See [the integration guide](docs/northstar-integration.md) for schema,
permissions, API and verification commands. Claude responses retain their
Snowflake thread/run IDs, tool traces, model, token usage, snapshot and prompt version. The engine
owns all approval-critical calculations; model output is advisory.

The historical `/api/graph/*` implementation and its managed Cortex Agent,
Search and Analyst contracts remain separate. NorthStar uses its own managed
Cortex coordinator and two specialist objects. [Live acceptance](docs/northstar-agent-live-verification.json)
verifies investigation and Company Brain, including their six threads and saved audits.
NorthStar does not claim Cortex Search or Analyst execution.
Drafts stay unsent; no real procurement or inventory transaction is performed.

Older datasets and acceptance results are preserved in
[archive_data](archive_data/README.md). They are historical, not current evidence.
For hosting configuration, see [deployment notes](docs/deployment.md).
