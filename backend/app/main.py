from __future__ import annotations

import secrets
import asyncio
from contextlib import asynccontextmanager, nullcontext, suppress
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import AppSettings
from .domain import EvidenceReply, HumanDecision, POLICY_VERSION
from .platform import PlatformError, SnowflakePlatform
from .snowflake_client import query
from .storage import Conflict, Store
from .workflow import Workflow
from .cases import CaseService, detect_risks
from .lifecycle_models import CaseBinding, DispatchInput, ExecutionEventInput, RiskEventInput, ScenarioProposal
from .monitor import FileFeedMonitor

@asynccontextmanager
async def lifespan(app):
    settings = AppSettings()
    monitor = FileFeedMonitor(get_workflow, settings.project_simulated_feed_path, settings.project_monitor_interval_seconds)
    app.state.monitor = monitor
    task = asyncio.create_task(monitor.run()) if monitor.path else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="sumo API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in AppSettings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Reviewer-Token"],
)
store = Store(
    AppSettings().project_store or str(Path(__file__).resolve().parents[1] / ".runtime" / "project.sqlite3")
)
# Offline startup does not require Snowflake credentials or trigger cloud I/O.
workflow = Workflow(store)


def enqueue(wf, run_id, background):
    def queue(run):
        wf.check_deadline(run)
        if run["status"] not in ("CREATED", "INVESTIGATING", "WAITING_EVIDENCE", "DEFERRED", "FAILED"):
            raise Conflict("This checkpoint cannot start another investigation")
        run["status"] = "INVESTIGATING"

    run = store.mutate(run_id, queue)
    background.add_task(wf.advance, run_id)
    return run


def get_workflow():
    return workflow


def ensure_live(wf):
    if wf.platform is None:
        wf.platform = SnowflakePlatform()


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["offline", "live"] = "offline"
    execution: Literal["concurrent", "sequential", "single", "deterministic"] = "concurrent"
    policy_version: str = POLICY_VERSION
    case_id: str | None = None


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    checkpoint: int


class EvaluationInput(BaseModel):
    frozen_id: str


class AnalystInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class BrainProposalInput(Binding):
    question: str = Field(min_length=1, max_length=2000)


@app.post("/api/graph/runs/{run_id}/scenario-proposal")
def scenario_proposal(run_id: str, payload: BrainProposalInput):
    from .brain import propose_scenario

    wf = get_workflow()
    with wf.owner(run_id):
        run = store.get(run_id)
        wf.binding(run, payload.model_dump())
        if run["status"] != "WAITING_DECISION":
            raise Conflict("The current assessment must be waiting for a decision")
        return propose_scenario(payload.question, run)


@app.exception_handler(Conflict)
def conflict_handler(request: Request, error: Conflict):
    return JSONResponse(status_code=409, content={"error": str(error), "code": "STALE_OR_BUSY_CHECKPOINT"})


@app.exception_handler(KeyError)
def missing_handler(request: Request, error: KeyError):
    return JSONResponse(
        status_code=404, content={"error": "Assessment or artifact not found", "code": "NOT_FOUND"}
    )


@app.exception_handler(ValueError)
def validation_handler(request: Request, error: ValueError):
    return JSONResponse(status_code=400, content={"error": str(error), "code": "INVALID_OPERATION"})


@app.exception_handler(PlatformError)
def platform_handler(request: Request, error: PlatformError):
    return JSONResponse(status_code=503, content={"error": str(error), "code": error.code})


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/snowflake/health")
def snowflake_health():
    try:
        row = query(
            "SELECT CURRENT_VERSION() AS VERSION, CURRENT_ROLE() AS ROLE, CURRENT_DATABASE() AS DATABASE, CURRENT_WAREHOUSE() AS WAREHOUSE"
        )[0]
        return {"status": "ok", **{k.lower(): v for k, v in row.items()}}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "code": "SNOWFLAKE_UNAVAILABLE",
                "error": "Snowflake connection unavailable. Check backend credentials, role and warehouse.",
            },
        )


@app.get("/api/snowflake/workspace")
def snowflake_workspace():
    from .config import get_settings
    from .snowflake_links import workspace_links

    try:
        return workspace_links(get_settings())
    except ValueError:
        return {
            "status": "unconfigured",
            "note": "Configure the Snowflake account, database, and optional SNOWFLAKE_SNOWSIGHT_URL to enable console links.",
        }


@app.get("/api/graph/runs")
def runs():
    return {"runs": store.list()}


@app.get("/api/cases")
def cases():
    return {"cases": [{k: v for k, v in c.items() if k not in ("snapshot", "events")} for c in store.case_list()]}


@app.get("/api/monitor/status")
def monitor_status():
    monitor = getattr(app.state, "monitor", None)
    return monitor.status if monitor else {"enabled": False, "adapter": "local_simulated_file"}


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str):
    return store.case_get(case_id)


@app.post("/api/cases/{case_id}/assessments", status_code=202)
def case_assessment(case_id: str, payload: CaseBinding, background: BackgroundTasks):
    wf = get_workflow()
    if store.case_get(case_id)["mode"] == "live":
        ensure_live(wf)
    run = CaseService(wf).reassess(case_id, payload.revision)
    background.add_task(wf.advance, run["id"])
    return run


@app.post("/api/risk-events", status_code=202)
def risk_event(payload: RiskEventInput, background: BackgroundTasks):
    wf = get_workflow()
    result = CaseService(wf).ingest(payload)
    if result["run"]["status"] == "CREATED" and result["case"]["events"][-1]["validation"] == "CONFIRMED":
        background.add_task(wf.advance, result["run"]["id"])
    return result


@app.post("/api/monitor/scan", status_code=202)
def monitor_scan(background: BackgroundTasks):
    """Explicit local scan; no cloud polling, inference, or background scheduler."""
    from .sources import OfflineSource

    snapshot = OfflineSource().load_snapshot()
    local_cases = [c for c in store.case_list() if c["mode"] == "offline" and c.get("snapshot")]
    if local_cases:
        from .domain import EvidenceSnapshot

        snapshot = EvidenceSnapshot.model_validate(local_cases[0]["snapshot"])
    po = snapshot.case["primary_po"]
    from .domain import fingerprint

    event_id = "scan-" + fingerprint({"company": snapshot.company, "scope": snapshot.scope, "po": po["id"], "receipt": po["supplier_confirmed_eta"], "quantity": po["quantity"]})[:24]
    prior = store.operational_event("risk:" + event_id)
    if prior:
        case = store.case_get(prior["case_id"])
        return {"case": case, "run": store.get(case["active_run_id"]), "duplicate": True}
    event = RiskEventInput(
        id=event_id, case_id=local_cases[0]["id"] if local_cases else None,
        source="simulated_supplier", source_reference=po["confirmed_eta_evidence"],
        observed_at=snapshot.as_of, po_id=po["id"], part=po["part"],
        location=snapshot.case["internal_transfer"]["to"], usable_receipt=po["supplier_confirmed_eta"],
    )
    wf = get_workflow()
    result = CaseService(wf).ingest(event)
    if result["run"]["status"] == "CREATED":
        background.add_task(wf.advance, result["run"]["id"])
    return result


@app.post("/api/cases/{case_id}/dispatch")
def dispatch(case_id: str, payload: DispatchInput):
    return CaseService(get_workflow()).dispatch(case_id, payload)


@app.post("/api/cases/{case_id}/execution-events", status_code=202)
def execution_event(case_id: str, payload: ExecutionEventInput, background: BackgroundTasks):
    wf = get_workflow()
    result = CaseService(wf).execution_event(case_id, payload)
    if result["run"]["status"] == "CREATED":
        background.add_task(wf.advance, result["run"]["id"])
    return result


@app.get("/api/risk-inbox")
def risk_inbox():
    from .engine import analyze_operations, discover_sources
    from .sources import OfflineSource

    snapshot = OfflineSource().load_snapshot()
    cases = [c for c in store.case_list() if c["mode"] == "offline"]
    if cases and cases[0].get("snapshot"):
        from .domain import EvidenceSnapshot

        snapshot = EvidenceSnapshot.model_validate(cases[0]["snapshot"])
    alerts = detect_risks(snapshot)
    for case in cases:
        for event in case["events"]:
            if event.get("validation") == "UNVERIFIED":
                alerts.append({"id": event["id"], "case_id": case["id"], "level": "Medium", "title": "Potential disruption reported", "supplier": event["source_reference"], "text": event["po_id"], "detail": "Awaiting an authoritative supplier confirmation", "icon": "alert", "destination": "evidence", "validation": "UNVERIFIED"})
    return {
        "mode": "offline",
        "snapshot": snapshot.model_dump(mode="json"),
        "operations": analyze_operations(snapshot).model_dump(mode="json"),
        "sourcing": discover_sources(snapshot).model_dump(mode="json"),
        "alerts": alerts,
        "case_id": cases[0]["id"] if cases else None,
    }


@app.post("/api/graph/runs/{run_id}/scenario")
def scenario(run_id: str, payload: ScenarioProposal):
    from .domain import EvidenceSnapshot
    from .scenarios import build_scenario

    wf = get_workflow()
    with wf.owner(run_id):
        run = store.get(run_id)
        wf.binding(run, payload.model_dump())
        if run["status"] != "WAITING_DECISION":
            raise Conflict("Complete source qualification before creating a scenario")
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        wf.reserve(run_id, "engine_evaluations")
        baseline = next((p for p in run["plans"] if p["id"] == run["recommended_plan_id"]), None)
        result = build_scenario(snapshot, payload, run.get("scenarios", []), run_id, baseline)
        plan = result["plan"]
        plans = run["plans"]
        if result["approval_eligible"] and not any(p["id"] == plan["id"] for p in plans):
            plans = [*plans, plan]
        scenarios = [*run.get("scenarios", []), result]
        store.artifact({"kind": "scenario_version", "run_id": run_id, **result})
        wf.event(run_id, "engine", "scenario_evaluated", f"Evaluated manager scenario: {payload.name}")
        impacts = {**run.get("plan_impacts", {}), plan["id"]: result["impact"]}
        return wf.checkpoint(run_id, plans=plans, scenarios=scenarios, plan_impacts=impacts)


@app.post("/api/graph/runs", status_code=202)
def create_run(
    payload: CreateRun, background: BackgroundTasks, idempotency_key: str | None = Header(default=None)
):
    wf = get_workflow()
    if payload.mode == "live":
        ensure_live(wf)
    with wf.owner(payload.case_id) if payload.case_id else nullcontext():
        active = store.case_get(payload.case_id)["active_run_id"] if payload.case_id else None
        with wf.owner(active) if active else nullcontext():
            run = wf.create(**payload.model_dump(), key=idempotency_key)
    if run["status"] == "CREATED":
        background.add_task(wf.advance, run["id"])
    return run


@app.get("/api/graph/runs/{run_id}")
def get_run(run_id: str):
    run = store.get(run_id)
    if (
        run["mode"] == "live"
        and run.get("snapshot")
        and run["status"] in ("WAITING_DECISION", "DRAFTS_READY")
    ):
        wf = get_workflow()
        ensure_live(wf)
        run = wf.ensure_current(run_id)
    if run.get("case_id"):
        case = store.case_get(run["case_id"])
        return {**run, "decision_case": {k: v for k, v in case.items() if k != "snapshot"}}
    return run


@app.get("/api/graph/runs/{run_id}/events")
def events(run_id: str):
    return {"events": store.get(run_id)["events"]}


@app.get("/api/graph/runs/{run_id}/drafts")
def drafts(run_id: str):
    return {"drafts": get_run(run_id)["drafts"]}


@app.post("/api/graph/runs/{run_id}/demo-confirmations", status_code=202)
def demo_confirmations(run_id: str, payload: Binding, background: BackgroundTasks):
    wf = get_workflow()
    wf.release_demo(run_id, payload.model_dump())
    return enqueue(wf, run_id, background)


@app.post("/api/graph/runs/{run_id}/evidence", status_code=202)
def evidence(run_id: str, payload: EvidenceReply, background: BackgroundTasks):
    wf = get_workflow()
    if store.get(run_id)["mode"] == "live":
        ensure_live(wf)
    run = wf.evidence(run_id, payload)
    if run["mode"] == "live" and payload.source_kind == "human_clarification":
        return run
    return enqueue(wf, run_id, background)


@app.post("/api/graph/runs/{run_id}/decision")
def decision(run_id: str, payload: HumanDecision, x_reviewer_token: str | None = Header(default=None)):
    wf = get_workflow()
    if store.get(run_id)["mode"] == "live":
        ensure_live(wf)
        manager = AppSettings().project_live_reviewer_id
        token = AppSettings().project_live_reviewer_token
        if (
            not manager
            or not token
            or payload.actor != manager
            or not secrets.compare_digest(x_reviewer_token or "", token)
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "A configured authorized manager is required for a live decision.",
                    "code": "MANAGER_AUTHORITY_REQUIRED",
                },
            )
    return wf.decide(run_id, payload)


@app.post("/api/graph/runs/{run_id}/refresh", status_code=202)
def refresh(run_id: str, payload: Binding, background: BackgroundTasks):
    wf = get_workflow()
    if store.get(run_id)["mode"] == "live":
        ensure_live(wf)
    wf.refresh(run_id, payload.model_dump())
    return enqueue(wf, run_id, background)


@app.post("/api/graph/runs/{run_id}/resume", status_code=202)
def resume(run_id: str, payload: Binding, background: BackgroundTasks):
    wf = get_workflow()
    if store.get(run_id)["mode"] == "live":
        ensure_live(wf)
    wf.resume(run_id, payload.model_dump())
    return enqueue(wf, run_id, background)


@app.post("/api/graph/runs/{run_id}/cancel")
def cancel(run_id: str, payload: Binding):
    return get_workflow().cancel(run_id, payload.model_dump())


@app.post("/api/graph/runs/{run_id}/evaluate")
def evaluate(run_id: str, payload: EvaluationInput):
    from .evaluation.replay import replay

    wf = get_workflow()
    if store.get(run_id)["mode"] == "live":
        ensure_live(wf)
    with wf.owner(run_id):
        return replay(store, run_id, payload.frozen_id, wf.platform)


@app.post("/api/graph/runs/{run_id}/analyst")
def analyst(run_id: str, payload: AnalystInput):
    from .domain import EvidenceSnapshot

    run = store.get(run_id)
    if run["mode"] != "live":
        return {"status": "not_run", "note": "Cortex Analyst is a live-only investigative surface."}
    wf = get_workflow()
    ensure_live(wf)
    return wf.platform.analyst(payload.question, EvidenceSnapshot.model_validate(run["snapshot"]))


@app.post("/api/graph/runs/{run_id}/sync")
def sync(run_id: str):
    wf = get_workflow()
    if store.get(run_id)["mode"] == "live":
        ensure_live(wf)
        wf.flush(run_id)
    return {"pending_records": store.pending_exports()}
