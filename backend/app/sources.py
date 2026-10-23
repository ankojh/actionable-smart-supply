"""Explicit offline source; never used as fallback for a live error."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .domain import Document, EvidenceSnapshot, fingerprint

DATA = Path(__file__).resolve().parents[1] / "data"
DEMO = Path(__file__).resolve().parents[1] / "demo"


def document(identifier: str, text: str, available_at: str, kind="synthetic_source") -> Document:
    import hashlib

    return Document(
        id=identifier,
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        available_at=available_at,
        source_kind=kind,
    )


def snapshot_from(case: dict[str, Any], documents: list[Document], as_of: str) -> EvidenceSnapshot:
    documents = sorted(documents, key=lambda d: d.id)
    material = {"case": case, "documents": [d.model_dump() for d in documents], "as_of": as_of}
    digest = fingerprint(material)
    return EvidenceSnapshot(
        id="snap-" + digest[:20],
        as_of=as_of,
        fingerprint=digest,
        case=case,
        documents=tuple(sorted(documents, key=lambda d: d.id)),
    )


class OfflineSource:
    def load_snapshot(self, company="synthetic-company", scope="shortage-recovery-v1", as_of=None):
        if company != "synthetic-company" or scope != "shortage-recovery-v1":
            raise ValueError("Source scope is not authorized")
        case = json.loads((DATA / "initial_case.json").read_text())
        at = case["as_of"]
        docs = [document(d["id"], d["text"], at) for d in case.pop("initial_documents")]
        for key in ("inventory_on_hand", "allocation_ledger", "production_orders", "candidate_offers"):
            for record in case[key]:
                docs.append(
                    document(record["id"], json.dumps(record, sort_keys=True), at, "operational_record")
                )
        for key in ("primary_po", "internal_transfer"):
            record = case[key]
            docs.append(document(record["id"], json.dumps(record, sort_keys=True), at, "operational_record"))
        case["confirmations"] = {}
        return snapshot_from(case, docs, at)

    def search(self, snapshot, query):
        terms = set(query.lower().split())
        return [
            d
            for d in snapshot.documents
            if d.available_at <= snapshot.as_of and (terms & set(d.text.lower().split()))
        ][:12]

    def release_confirmations(self, snapshot):
        # This file is opened only after an explicit offline release action.
        events = json.loads((DEMO / "supplier_replies.json").read_text())["events"]
        case = copy.deepcopy(snapshot.case)
        docs = list(snapshot.documents)
        for event in events:
            candidate_id = "OFFER-" + event["id"].removeprefix("EV-")
            fields = {**event["validated_fields"], "candidate_id": candidate_id}
            doc = event["document"]
            text = doc["text"] + "\nVerified source fields: " + json.dumps(fields, sort_keys=True)
            new_doc = document(doc["id"], text, event["available_at"], "supplier_confirmation")
            docs = [d for d in docs if d.id != new_doc.id] + [new_doc]
            case["confirmations"][candidate_id] = {**fields, "document_id": new_doc.id}
        return snapshot_from(case, docs, max(e["available_at"] for e in events))

    def ingest_clarification(self, snapshot, reply):
        # Human assertions cannot update reservation, price, quantity or policy fields.
        if reply.source_kind != "human_clarification" or reply.claims:
            raise ValueError(
                "Use a published authoritative source confirmation; human text cannot validate claims"
            )
        if reply.available_at > snapshot.as_of:
            raise ValueError("Evidence is not available at the current scenario time")
        if any(d.id == reply.document_id for d in snapshot.documents):
            raise ValueError("Document IDs are immutable; supply a new ID")
        doc = document(reply.document_id, reply.text, reply.available_at, "human_clarification")
        return snapshot_from(copy.deepcopy(snapshot.case), [*snapshot.documents, doc], snapshot.as_of)
