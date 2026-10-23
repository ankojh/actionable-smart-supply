"""Versioned immutable contracts shared by the engine, sources and workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "recovery-v1"
POLICY_VERSION = "cost-first-v1"
GRAPH_VERSION = "case-lifecycle-v2"


def fingerprint(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Document(Contract):
    id: str
    text: str
    sha256: str
    available_at: str
    company: str = "synthetic-company"
    scope: str = "shortage-recovery-v1"
    version: str = SCHEMA_VERSION
    source_kind: str = "synthetic_source"


class EvidenceSnapshot(Contract):
    id: str
    company: str = "synthetic-company"
    scope: str = "shortage-recovery-v1"
    schema_version: str = SCHEMA_VERSION
    policy_version: str = POLICY_VERSION
    as_of: str
    fingerprint: str
    case: dict[str, Any]
    documents: tuple[Document, ...]


class MaterialNeed(Contract):
    production_start: str
    cumulative_required: int
    cumulative_recovery: int
    order_ids: tuple[str, ...]


class OperationsFinding(Contract):
    required_drivers: int
    usable_drivers: int
    uncovered_drivers: int
    warehouse_on_hand: int
    warehouse_transferable: int
    original_po_obligation_usd: float
    time_phased: tuple[MaterialNeed, ...]
    affected_orders: tuple[str, ...]
    citations: tuple[str, ...]
    unknowns: tuple[str, ...] = ()


class Candidate(Contract):
    id: str
    supplier: str
    kind: Literal["transfer", "purchase"]
    status: Literal["supported", "rejected", "unresolved"]
    max_quantity: int = 0
    moq: int = 1
    multiple: int = 1
    usable_receipt: str | None = None
    reservation_deadline: str | None = None
    unit_price_usd: float | None = None
    freight_usd: float | None = None
    reasons: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()


class RecoveryCandidateSet(Contract):
    candidates: tuple[Candidate, ...]
    citations: tuple[str, ...]


class QualificationReport(Contract):
    candidates: tuple[Candidate, ...]
    issues: tuple[str, ...]
    work_orders: tuple[str, ...] = ()
    citations: tuple[str, ...]


class PlanAction(Contract):
    candidate_id: str
    supplier: str
    kind: Literal["transfer", "purchase"]
    quantity: int
    usable_receipt: str
    expenditure_usd: float | None
    citations: tuple[str, ...]


class ScheduleRow(Contract):
    order_id: str
    start_date: str
    ship_date: str
    robots: int
    on_time_robots: int


class RecoveryPlanEvaluation(Contract):
    id: str
    actions: tuple[PlanAction, ...]
    expenditure_usd: float | None
    excess_drivers: int
    remaining_shortage: int
    on_time_units: int
    whole_orders: int
    feasible: bool
    within_authority: bool
    schedule: tuple[ScheduleRow, ...]
    reasons: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION


class EvidenceRequest(Contract):
    id: str
    run_id: str
    snapshot_id: str
    company: str
    scope: str
    policy_version: str
    checkpoint: int
    candidate_id: str
    required_fields: tuple[str, ...]
    question: str
    status: str = "DRAFT_NOT_SUBMITTED"
    resolved: bool = False


class FrozenRecommendation(Contract):
    id: str
    run_id: str
    snapshot_id: str
    input_fingerprint: str
    policy_version: str
    kind: Literal["recommended", "selected"]
    plan: RecoveryPlanEvaluation
    frozen_at: str
    evaluator_version: str = "replay-v1"


class HumanDecision(Contract):
    snapshot_id: str
    checkpoint: int
    policy_version: str = POLICY_VERSION
    company: str = "synthetic-company"
    scope: str = "shortage-recovery-v1"
    action: Literal["approve", "alternative", "defer"]
    plan_id: str | None = None
    reason: str = Field(min_length=1, max_length=2000)
    actor: str = Field(min_length=1, max_length=100)


class EvidenceReply(Contract):
    request_id: str
    snapshot_id: str
    checkpoint: int
    policy_version: str = POLICY_VERSION
    company: str = "synthetic-company"
    scope: str = "shortage-recovery-v1"
    document_id: str
    text: str = Field(min_length=1, max_length=16000)
    available_at: str
    source_kind: Literal["human_clarification", "supplier_confirmation"] = "human_clarification"
    source_reference: str = Field(min_length=1, max_length=500)
    # Structured claims are admitted only if backed by a trusted source record.
    claims: dict[str, Any] = Field(default_factory=dict)


class OutcomeReport(Contract):
    frozen_id: str
    evaluator_version: str = "replay-v1"
    label: str = "Synthetic deterministic replay; sensitivity is not probability"
    on_time_units: int
    whole_orders: int
    remaining_shortage: int
    expenditure_usd: float | None
    baseline_on_time_units: int
    change_on_time_units: int
    forecast_error_units: int
    assumptions: tuple[str, ...]


def utc_now() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()
