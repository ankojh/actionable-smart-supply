from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .config import AppSettings
from .domain import EvidenceReply, HumanDecision, POLICY_VERSION
from .platform import PlatformError, SnowflakePlatform
from .snowflake_client import query
from .storage import Conflict, Store
from .workflow import Workflow

app = FastAPI(title="The project API")
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


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    checkpoint: int


class EvaluationInput(BaseModel):
    frozen_id: str


class AnalystInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ScenarioInput(Binding):
    name: str = Field(min_length=1, max_length=80)
    quantities: dict[str, StrictInt] = Field(max_length=20)


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


@app.get("/api/risk-inbox")
def risk_inbox():
    from .engine import analyze_operations, discover_sources
    from .sources import OfflineSource

    snapshot = OfflineSource().load_snapshot()
    return {
        "mode": "offline",
        "snapshot": snapshot.model_dump(mode="json"),
        "operations": analyze_operations(snapshot).model_dump(mode="json"),
        "sourcing": discover_sources(snapshot).model_dump(mode="json"),
    }


@app.post("/api/graph/runs/{run_id}/scenario")
def scenario(run_id: str, payload: ScenarioInput):
    from .domain import EvidenceSnapshot
    from .engine import evaluate_scenario

    wf = get_workflow()
    with wf.owner(run_id):
        run = store.get(run_id)
        wf.binding(run, payload.model_dump())
        if run["status"] != "WAITING_DECISION":
            raise Conflict("Complete source qualification before creating a scenario")
        snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
        plan = evaluate_scenario(snapshot, payload.quantities)
        wf.reserve(run_id, "engine_evaluations")
        plans = run["plans"]
        if plan.feasible and plan.within_authority and not any(p["id"] == plan.id for p in plans):
            plans = [*plans, plan.model_dump(mode="json")]
        scenarios = [
            s
            for s in run.get("scenarios", [])
            if s["snapshot_id"] == snapshot.id and s["plan"]["id"] != plan.id
        ]
        scenarios.append(
            {"name": payload.name, "snapshot_id": snapshot.id, "plan": plan.model_dump(mode="json")}
        )
        wf.event(run_id, "engine", "scenario_evaluated", f"Evaluated manager scenario: {payload.name}")
        return wf.checkpoint(run_id, plans=plans, scenarios=scenarios)


@app.post("/api/graph/runs", status_code=202)
def create_run(
    payload: CreateRun, background: BackgroundTasks, idempotency_key: str | None = Header(default=None)
):
    wf = get_workflow()
    if payload.mode == "live":
        ensure_live(wf)
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
        return wf.ensure_current(run_id)
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
