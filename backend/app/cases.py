"""Persistent operational lifecycle. Only simulated adapters can mutate source facts.

Manual risk reports remain unverified. Live source admission still belongs to the
governed Snowflake publisher. No endpoint here sends a real PO or supplier message.
"""

import copy
import json
from contextlib import nullcontext

from .domain import EvidenceSnapshot, RecoveryPlanEvaluation, fingerprint, utc_now
from .engine import analyze_operations, discover_sources, validate_plan
from .platform import instant
from .scenarios import derived_snapshot, replace_confirmation
from .sources import OfflineSource, document
from .storage import Conflict


TERMINAL = {"RECEIVED", "FAILED", "CANCELLED"}


def detect_risks(snapshot):
    operations = analyze_operations(snapshot)
    candidates = discover_sources(snapshot).candidates
    po = snapshot.case["primary_po"]
    alerts = []
    if po.get("supplier_confirmed_eta") and instant(po["supplier_confirmed_eta"]) > instant(po["erp_eta"]):
        alerts.append(
            {
                "id": "delay-" + snapshot.id,
                "level": "Critical" if operations.uncovered_drivers else "Medium",
                "title": "Supply delay detected",
                "supplier": "Primary supplier",
                "text": f"{po['id']} · {po['part']}",
                "detail": f"Confirmed receipt {po['supplier_confirmed_eta']} replaces ERP ETA {po['erp_eta']}",
                "icon": "gear",
                "destination": "overview",
                "validation": "CONFIRMED",
                "citations": [po["confirmed_eta_evidence"]],
            }
        )
    unresolved = [c for c in candidates if c.status == "unresolved"]
    rejected = [c for c in candidates if c.status == "rejected"]
    for group, level, title, destination in (
        (unresolved, "High", "Supplier confirmations needed", "recovery"),
        (rejected, "Medium", "Source qualification failed", "suppliers"),
    ):
        if group:
            alerts.append(
                {
                    "id": destination + snapshot.id,
                    "level": level,
                    "title": title,
                    "supplier": ", ".join(c.supplier for c in group),
                    "text": snapshot.case["policies"]["product_required"],
                    "detail": "; ".join(dict.fromkeys(r for c in group for r in c.reasons)),
                    "icon": "shield",
                    "destination": destination,
                    "validation": "EVIDENCE_REVIEW",
                    "citations": sorted({d for c in group for d in c.citations}),
                }
            )
    return alerts


class SimulatedActionAdapter:
    """Deterministic transaction identity makes retry/restart safe without real I/O."""

    def submit(self, case_id, run_id, draft_id, action):
        return {
            "id": "execution-" + fingerprint({"case": case_id, "run": run_id, "draft": draft_id})[:24],
            "transaction_id": "SIM-"
            + ("PO-" if action["kind"] == "purchase" else "TRANSFER-")
            + fingerprint({"case": case_id, "draft": draft_id})[:16],
            "adapter": "simulated",
            "status": "SUBMITTED",
            "run_id": run_id,
            "draft_id": draft_id,
            "action": action,
            "submitted_at": utc_now(),
        }


class CaseService:
    def __init__(self, workflow):
        self.workflow, self.store = workflow, workflow.store

    def _revision(self, case, revision):
        if case["revision"] != revision:
            raise Conflict("Decision case changed; reload its current revision")

    def _offline(self, case):
        if case["mode"] != "offline":
            raise Conflict(
                "Simulated events and transactions are restricted to offline cases; live evidence requires governed publication"
            )

    def reassess(self, case_id, revision):
        with self.workflow.owner(case_id):
            case = self.store.case_get(case_id)
            self._revision(case, revision)
            with self.workflow.owner(case["active_run_id"]):
                return self.workflow.create(mode=case["mode"], case_id=case_id)

    def ingest(self, payload):
        raw = payload.model_dump(mode="json")
        digest = fingerprint(raw)
        event_id = "risk:" + payload.id
        with self.workflow.owner("risk-ingestion"):
            prior = self.store.operational_event(event_id)
            if prior:
                if prior["hash"] != digest:
                    raise Conflict("Event ID was reused with different content")
                case = self.store.case_get(prior["case_id"])
                run = (
                    self._event_assessment(case, event_id)
                    if prior["payload"]["validation"] == "CONFIRMED"
                    else self.store.get(case["active_run_id"])
                )
                return {"case": self.store.case_get(case["id"]), "run": run, "duplicate": True}
            if payload.case_id:
                case = self.store.case_get(payload.case_id)
            else:
                matches = [
                    c
                    for c in self.store.case_list()
                    if c["mode"] == "offline"
                    and c.get("snapshot")
                    and c["snapshot"]["case"]["primary_po"]["id"] == payload.po_id
                    and c["snapshot"]["case"]["primary_po"]["part"] == payload.part
                ]
                case = matches[0] if matches else None
                if case is None:
                    base = OfflineSource().load_snapshot()
                    self._validate_resource(base, payload)
                    if instant(payload.observed_at.isoformat()) < instant(base.as_of):
                        raise ValueError("An event cannot predate the current evidence snapshot")
                    if payload.source == "simulated_supplier":
                        self._risk_snapshot(base, payload)
                    run = self.workflow.create()
                    case = self.store.case_mutate(
                        run["case_id"], lambda c: c.update(snapshot=base.model_dump(mode="json"))
                    )
            self._offline(case)
            with self.workflow.owner(case["id"]), self.workflow.owner(case["active_run_id"]):
                snapshot = EvidenceSnapshot.model_validate(case["snapshot"])
                self._validate_resource(snapshot, payload)
                if instant(payload.observed_at.isoformat()) < instant(snapshot.as_of):
                    raise ValueError("An event cannot predate the current evidence snapshot")
                confirmed = payload.source == "simulated_supplier"
                updated = self._risk_snapshot(snapshot, payload) if confirmed else snapshot
                active = self.store.get(case["active_run_id"])
                event = {
                    **raw,
                    "id": event_id,
                    "validation": "CONFIRMED" if confirmed else "UNVERIFIED",
                    "material_change": confirmed and active["status"] != "CREATED",
                    "snapshot_id": updated.id,
                }

                def apply(target):
                    if confirmed:
                        target.update(snapshot=updated.model_dump(mode="json"), status="REPLAN_REQUIRED")
                        for action in target["executions"]:
                            if action["status"] not in TERMINAL:
                                action["previous_status"] = action["status"]
                                action["status"] = "REVIEW_REQUIRED"

                case = self.store.record_case_event(case["id"], event, digest, apply)
                run = self._event_assessment(case, event_id) if confirmed else active
                return {"case": self.store.case_get(case["id"]), "run": run, "duplicate": False}

    def _event_assessment(self, case, event_id):
        run = self.store.get(case["active_run_id"])
        if run["status"] == "SUPERSEDED":
            return self.workflow.create(mode=case["mode"], case_id=case["id"], key="assessment:" + event_id)
        if run["status"] == "CREATED":
            return self.store.mutate(
                run["id"],
                lambda r: r.update(seed_snapshot=case["snapshot"], scenario_time=case["snapshot"]["as_of"]),
            )
        return run

    def _validate_resource(self, snapshot, payload):
        po = snapshot.case["primary_po"]
        if (
            payload.po_id != po["id"]
            or payload.part != po["part"]
            or payload.location != snapshot.case["internal_transfer"]["to"]
        ):
            raise ValueError("Event does not match the case PO, part and receiving location")

    def _risk_snapshot(self, snapshot, payload):
        case, docs = copy.deepcopy(snapshot.case), list(snapshot.documents)
        at = payload.observed_at.isoformat()
        tag = "DOC-EVENT-" + fingerprint(payload.model_dump(mode="json"))[:20]
        docs.append(
            document(
                tag, json.dumps(payload.model_dump(mode="json"), sort_keys=True), at, "trusted_supplier_feed"
            )
        )
        if payload.kind == "supplier_delay":
            if payload.usable_receipt is None or instant(payload.usable_receipt.isoformat()) < instant(at):
                raise ValueError("A supplier update needs a receipt at or after the observation time")
            case["primary_po"].update(
                supplier_confirmed_eta=payload.usable_receipt.isoformat(), confirmed_eta_evidence=tag
            )
            if payload.quantity is not None:
                case["primary_po"]["quantity"] = payload.quantity
        elif payload.kind == "supplier_commitment":
            if not payload.candidate_id or (payload.usable_receipt is None and payload.quantity is None):
                raise ValueError("A commitment change needs a source and changed receipt or quantity")
            changes = {}
            if payload.usable_receipt:
                changes["usable_receipt"] = payload.usable_receipt.isoformat()
            if payload.quantity is not None:
                changes["confirmed_max_quantity"] = payload.quantity
            replace_confirmation(case, docs, payload.candidate_id, changes, at, payload.id)
        else:
            if payload.quantity is None:
                raise ValueError("Inventory events require the physical on-hand quantity")
            rows = [
                r
                for r in case["inventory_on_hand"]
                if r["location"] == payload.location and r["part"] == payload.part
            ]
            if len(rows) != 1:
                raise ValueError("Inventory correction requires one unambiguous stock record")
            previous = rows[0]["id"]
            allocated = sum(a["quantity"] for a in case["allocation_ledger"] if a["inventory_id"] == previous)
            if payload.quantity < allocated:
                raise ValueError(
                    "Physical stock is below existing allocations; reconcile commitments before admission"
                )
            change = payload.quantity - rows[0]["on_hand"]
            opening = rows[0].get("opening_on_hand", rows[0]["on_hand"])
            rows[0].update(id="INV-" + tag, on_hand=payload.quantity, opening_on_hand=opening, as_of=at)
            case.setdefault("inventory_adjustments", []).append(
                {
                    "id": tag,
                    "location": payload.location,
                    "part": payload.part,
                    "quantity_delta": change,
                    "usable_at": at,
                }
            )
            for allocation in case["allocation_ledger"]:
                if allocation["inventory_id"] == previous:
                    allocation["inventory_id"] = rows[0]["id"]
                    allocation["id"] = allocation["id"] + "-" + fingerprint(tag)[:8]
                    docs.append(
                        document(
                            allocation["id"], json.dumps(allocation, sort_keys=True), at, "operational_record"
                        )
                    )
            docs.append(
                document(rows[0]["id"], json.dumps(rows[0], sort_keys=True), at, "operational_record")
            )
        return derived_snapshot(snapshot, case, docs, at)

    def dispatch(self, case_id, payload):
        with self.workflow.owner(case_id), self.workflow.owner(payload.run_id):
            case = self.store.case_get(case_id)
            self._offline(case)
            run = self.store.get(payload.run_id)
            if run.get("case_id") != case_id or case["active_run_id"] != run["id"]:
                raise Conflict("Only the current case assessment can submit transactions")
            existing = [a for a in case["executions"] if a["run_id"] == run["id"]]
            dispatch_hash = fingerprint(
                {k: v for k, v in payload.model_dump(mode="json").items() if k != "revision"}
            )
            if existing:
                if any(a.get("dispatch_hash") != dispatch_hash for a in existing):
                    raise Conflict("Simulated submission was retried with different authorization inputs")
                return case
            self._revision(case, payload.revision)
            self.workflow.binding(run, payload.model_dump())
            if (
                run["status"] != "DRAFTS_READY"
                or not run["decision"]
                or payload.actor != run["decision"]["actor"]
            ):
                raise Conflict("The approving reviewer must authorize the exact prepared plan")
            if any(a["status"] not in TERMINAL for a in case["executions"]):
                raise Conflict("Outstanding transactions must be reconciled before replacement submission")
            if case["snapshot"]["id"] != payload.snapshot_id:
                raise Conflict("Case evidence changed after approval")
            plan = next(p for p in run["plans"] if p["id"] == run["decision"]["plan_id"])
            snapshot = EvidenceSnapshot.model_validate(run["snapshot"])
            if not validate_plan(
                snapshot.model_copy(update={"as_of": self.workflow.authorization_time(run)}),
                RecoveryPlanEvaluation.model_validate(plan),
            ):
                raise Conflict("The plan is no longer feasible or its reservation expired")
            adapter = SimulatedActionAdapter()
            actions = [
                adapter.submit(case_id, run["id"], draft["id"], action)
                for draft, action in zip(run["drafts"], plan["actions"], strict=True)
            ]

            def apply(target):
                target["executions"].extend(
                    {
                        **a,
                        "dispatch_hash": dispatch_hash,
                        "plan_id": plan["id"],
                        "snapshot_id": snapshot.id,
                        "actor": payload.actor,
                        "reason": payload.reason,
                    }
                    for a in actions
                )
                target["status"] = "EXECUTING"

            return self.store.case_mutate(case_id, apply)

    def execution_event(self, case_id, payload):
        raw = payload.model_dump(mode="json")
        digest = fingerprint({k: v for k, v in raw.items() if k != "revision"})
        event_id = "execution:" + payload.id
        with self.workflow.owner(case_id):
            case = self.store.case_get(case_id)
            self._offline(case)
            prior = self.store.operational_event(event_id)
            if prior:
                if prior["case_id"] != case_id or prior["hash"] != digest:
                    raise Conflict("Execution event ID was reused")
                return {"case": case, "run": self.store.get(case["active_run_id"]), "duplicate": True}
            self._revision(case, payload.revision)
            with self.workflow.owner(case["active_run_id"]) if case["active_run_id"] else nullcontext():
                action = next(
                    (a for a in case["executions"] if a["transaction_id"] == payload.transaction_id), None
                )
                if action is None:
                    raise ValueError("Unknown case transaction")
                previous = action.get("last_observed_at", case["snapshot"]["as_of"])
                if instant(payload.observed_at.isoformat()) < max(
                    instant(previous), instant(case["snapshot"]["as_of"])
                ):
                    raise Conflict(
                        "Execution events must follow the current evidence and transaction timeline"
                    )
                allowed = {
                    "SUBMITTED": {"acknowledged", "delayed", "failed", "cancelled"},
                    "ACKNOWLEDGED": {"shipped", "received", "delayed", "failed", "cancelled"},
                    "IN_TRANSIT": {"received", "delayed", "failed"},
                    "REVIEW_REQUIRED": {
                        "received",
                        "delayed",
                        "cancelled",
                        "failed",
                        "acknowledged",
                        "shipped",
                    },
                }
                if payload.kind not in allowed.get(action["status"], set()):
                    raise Conflict("Invalid transaction transition; terminal receipts cannot be replayed")
                snapshot = EvidenceSnapshot.model_validate(case["snapshot"])
                updated = snapshot
                material = payload.kind in {"received", "delayed", "failed", "cancelled"}
                if payload.kind == "received":
                    updated = self._receipt(snapshot, action, payload)
                elif payload.kind == "delayed":
                    if payload.usable_receipt is None or instant(
                        payload.usable_receipt.isoformat()
                    ) < instant(payload.observed_at.isoformat()):
                        raise ValueError("A delay requires a future usable receipt")
                    data, docs = copy.deepcopy(snapshot.case), list(snapshot.documents)
                    if action["action"]["kind"] == "purchase":
                        replace_confirmation(
                            data,
                            docs,
                            action["action"]["candidate_id"],
                            {"usable_receipt": payload.usable_receipt.isoformat()},
                            payload.observed_at.isoformat(),
                            payload.id,
                        )
                    else:
                        data["internal_transfer"]["confirmed_usable_receipt"] = (
                            payload.usable_receipt.isoformat()
                        )
                    updated = derived_snapshot(snapshot, data, docs, payload.observed_at.isoformat())
                elif material:
                    # Failed/cancelled commitments must not remain purchasable reservations.
                    data, docs = copy.deepcopy(snapshot.case), list(snapshot.documents)
                    if action["action"]["kind"] == "purchase":
                        replace_confirmation(
                            data,
                            docs,
                            action["action"]["candidate_id"],
                            {"confirmed_max_quantity": 0},
                            payload.observed_at.isoformat(),
                            payload.id,
                        )
                    updated = derived_snapshot(snapshot, data, docs, payload.observed_at.isoformat())
                event = {
                    **raw,
                    "id": event_id,
                    "material_change": material,
                    "validation": "SIMULATED",
                    "snapshot_id": updated.id,
                }
                statuses = {
                    "acknowledged": "ACKNOWLEDGED",
                    "shipped": "IN_TRANSIT",
                    "received": "RECEIVED",
                    "delayed": "REVIEW_REQUIRED",
                    "failed": "FAILED",
                    "cancelled": "CANCELLED",
                }

                def apply(target):
                    transaction = next(a for a in target["executions"] if a["id"] == action["id"])
                    transaction.update(
                        status=statuses[payload.kind], last_observed_at=payload.observed_at.isoformat()
                    )
                    if payload.usable_receipt:
                        transaction["updated_receipt"] = payload.usable_receipt.isoformat()
                    target["snapshot"] = updated.model_dump(mode="json")
                    target["status"] = (
                        "RECEIPTS_COMPLETE"
                        if all(a["status"] == "RECEIVED" for a in target["executions"])
                        else "REPLAN_REQUIRED"
                        if material
                        else "EXECUTING"
                    )

                case = self.store.record_case_event(case_id, event, digest, apply)
                run = (
                    self._event_assessment(case, event_id)
                    if material
                    else self.store.get(case["active_run_id"])
                )
                return {"case": self.store.case_get(case_id), "run": run, "duplicate": False}

    def _receipt(self, snapshot, execution, payload):
        case, docs = copy.deepcopy(snapshot.case), list(snapshot.documents)
        action = execution["action"]
        at = payload.observed_at.isoformat()
        stock = {
            "id": "INV-RECEIPT-" + execution["id"],
            "location": case["internal_transfer"]["to"],
            "part": case["primary_po"]["part"],
            "on_hand": action["quantity"],
            "as_of": at,
            "receipt_at": at,
        }
        case["inventory_on_hand"].append(stock)
        docs.append(document(stock["id"], json.dumps(stock, sort_keys=True), at, "operational_record"))
        if action["kind"] == "purchase":
            claims = case["confirmations"][action["candidate_id"]]
            replace_confirmation(
                case,
                docs,
                action["candidate_id"],
                {"confirmed_max_quantity": max(0, claims["confirmed_max_quantity"] - action["quantity"])},
                at,
                payload.id,
            )
        else:
            remaining = action["quantity"]
            for donor in case["inventory_on_hand"]:
                if donor["location"] != case["internal_transfer"]["from"] or donor["part"] != stock["part"]:
                    continue
                allocated = sum(
                    a["quantity"] for a in case["allocation_ledger"] if a["inventory_id"] == donor["id"]
                )
                quantity = min(remaining, max(0, donor["on_hand"] - allocated))
                if quantity:
                    allocation = {
                        "id": "ALLOC-" + execution["id"] + donor["id"],
                        "inventory_id": donor["id"],
                        "quantity": quantity,
                        "purpose": "Executed simulated transfer",
                    }
                    case["allocation_ledger"].append(allocation)
                    docs.append(
                        document(
                            allocation["id"], json.dumps(allocation, sort_keys=True), at, "operational_record"
                        )
                    )
                    remaining -= quantity
            if remaining:
                raise Conflict("Donor stock changed; reconcile the transfer before receipt")
        return derived_snapshot(snapshot, case, docs, at)
