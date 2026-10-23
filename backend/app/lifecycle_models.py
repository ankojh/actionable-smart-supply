"""Inputs for versioned what-ifs and the simulated operational event boundary."""

from datetime import datetime
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from .domain import Contract


class SourceAssumption(Contract):
    usable_receipt: datetime | None = None
    moq: StrictInt | None = Field(default=None, ge=1, le=100000)
    multiple: StrictInt | None = Field(default=None, ge=1, le=100000)
    confirmed_max_quantity: StrictInt | None = Field(default=None, ge=0, le=100000)
    unit_price_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    freight_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ScenarioAssumptions(Contract):
    sources: dict[str, SourceAssumption] = Field(default_factory=dict, max_length=20)
    recovery_expenditure_limit_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    production_capacity_robots_per_day: StrictInt | None = Field(default=None, ge=1, le=10000)
    strategy: Literal["cost_first", "internal_first"] = "cost_first"


class ScenarioProposal(Contract):
    snapshot_id: str
    checkpoint: int
    name: str = Field(min_length=1, max_length=80)
    parent_id: str | None = None
    quantities: dict[str, StrictInt] = Field(default_factory=dict, max_length=20)
    assumptions: ScenarioAssumptions = Field(default_factory=ScenarioAssumptions)


class RiskEventInput(Contract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    case_id: str | None = None
    kind: Literal["supplier_delay", "supplier_commitment", "inventory_change"] = "supplier_delay"
    source: Literal["manual_report", "simulated_supplier"] = "manual_report"
    source_reference: str = Field(min_length=1, max_length=500)
    observed_at: datetime
    po_id: str = Field(min_length=1, max_length=100)
    part: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=100)
    candidate_id: str | None = None
    usable_receipt: datetime | None = None
    quantity: StrictInt | None = Field(default=None, ge=0, le=100000)


class CaseBinding(Contract):
    revision: int


class DispatchInput(CaseBinding):
    run_id: str
    snapshot_id: str
    checkpoint: int
    actor: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    adapter: Literal["simulated"] = "simulated"


class ExecutionEventInput(CaseBinding):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    transaction_id: str
    kind: Literal["acknowledged", "shipped", "received", "delayed", "failed", "cancelled"]
    observed_at: datetime
    source_reference: str = Field(min_length=1, max_length=500)
    usable_receipt: datetime | None = None

    @field_validator("source_reference")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A source reference is required")
        return value
