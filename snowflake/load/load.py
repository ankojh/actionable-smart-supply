"""Publish immutable fictional business evidence; future confirmations need explicit release.

Default mode validates and prints a sanitized summary without any Snowflake call.
Use --apply only after setup and cloud authorization. Never imports reference code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings
from app.domain import POLICY_VERSION, SCHEMA_VERSION, Document, fingerprint
from app.platform import TABLE_FIELDS, identifier, instant, snapshot_fingerprint
from app.snowflake_client import connection

DATA = Path(__file__).resolve().parent


def prepare(release_as_of: str | None = None):
    case = json.loads((DATA / "initial_case.json").read_text())
    as_of = release_as_of or case["as_of"]
    if instant(as_of) < instant(case["as_of"]):
        raise ValueError("Cannot publish before initial evidence time")
    documents = []
    metadata = {}

    def doc(doc_id, text, at, kind, claims=None, reference=None):
        document = Document(
            id=doc_id,
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            available_at=at,
            source_kind=kind,
        )
        documents.append(document)
        metadata[doc_id] = {
            "claims": claims or {},
            "reference": reference or "synthetic-business-records-v1/" + doc_id,
        }

    initial = case.pop("initial_documents")
    for row in initial:
        doc(row["id"], row["text"], case["as_of"], "synthetic_source")
    for table, (key, many) in TABLE_FIELDS.items():
        if table in {"POLICIES", "PRODUCTION_CAPACITY"}:
            continue
        values = case[key] if many else [case[key]]
        for row in values:
            doc(
                row["id"],
                json.dumps(row, sort_keys=True),
                case["as_of"],
                "operational_record",
            )
    case["confirmations"] = {}
    # Explicit release only: initial loading never opens the withheld feed file.
    if release_as_of:
        for event in json.loads((DATA / "supplier_confirmations.json").read_text())["events"]:
            if instant(event["available_at"]) > instant(as_of):
                continue
            claims = event["claims"]
            if claims.get("candidate_id") != event["candidate_id"]:
                raise ValueError("Invalid authoritative feed")
            marker = "Verified source fields: "
            if marker not in event["text"] or json.loads(event["text"].split(marker, 1)[1]) != claims:
                raise ValueError("Normalized claims do not match trusted source")
            doc(
                event["document_id"],
                event["text"],
                event["available_at"],
                "supplier_confirmation",
                claims,
                event["source_reference"],
            )
            case["confirmations"][event["candidate_id"]] = {
                **claims,
                "document_id": event["document_id"],
            }
    case["initial_documents"] = sorted(initial, key=lambda row: row["id"])
    documents.sort(key=lambda d: d.id)
    digest = snapshot_fingerprint(case, documents)
    snapshot_id = "sf-snap-" + digest[:20]
    return {
        "id": snapshot_id,
        "as_of": as_of,
        "case": case,
        "documents": documents,
        "metadata": metadata,
        "fingerprint": digest,
    }


def merge(cursor, table, columns, values, keys):
    select = ",".join(
        ("PARSE_JSON(%s)" if key in {"PAYLOAD", "HEADER", "CLAIMS"} else "%s") + " AS " + key
        for key in columns
    )
    on = " AND ".join("T." + key + "=S." + key for key in keys)
    cursor.execute(
        f"MERGE INTO {table} T USING (SELECT {select}) S ON {on} WHEN NOT MATCHED THEN INSERT ({','.join(columns)}) VALUES ({','.join('S.' + key for key in columns)})",
        tuple(values),
    )


def publish(bundle, settings):
    prefix = identifier(settings.database or "") + "." + identifier(settings.app_schema)
    case = bundle["case"]
    snapshot_id = bundle["id"]
    at = bundle["as_of"]
    company = settings.company
    scope = settings.scope
    if company != "synthetic-company" or scope != "shortage-recovery-v1":
        raise ValueError("Loader only supports its dedicated fictional scope")
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT FINGERPRINT,STATUS FROM {prefix}.SNAPSHOTS WHERE SNAPSHOT_ID=%s",
            (snapshot_id,),
        )
        prior = cur.fetchall()
        if prior:
            if len(prior) != 1 or prior[0] != (bundle["fingerprint"], "PUBLISHED"):
                raise ValueError("Immutable snapshot conflict; investigate with setup role")
            return
        try:
            cur.execute("BEGIN")
            for table, (key, many) in TABLE_FIELDS.items():
                values = case[key] if many else [case[key]]
                for ordinal, row in enumerate(values):
                    source_id = row.get("id", key)
                    merge(
                        cur,
                        prefix + "." + table,
                        [
                            "COMPANY",
                            "SCOPE",
                            "SCHEMA_VERSION",
                            "SNAPSHOT_ID",
                            "SOURCE_ID",
                            "ORDINAL",
                            "PAYLOAD",
                            "SHA256",
                            "AVAILABLE_AT",
                        ],
                        [
                            company,
                            scope,
                            SCHEMA_VERSION,
                            snapshot_id,
                            source_id,
                            ordinal,
                            json.dumps(row),
                            fingerprint(row),
                            at,
                        ],
                        [
                            "COMPANY",
                            "SCOPE",
                            "SCHEMA_VERSION",
                            "SNAPSHOT_ID",
                            "SOURCE_ID",
                        ],
                    )
            bom = {
                "part": "DRV-24-B",
                "revision": "B",
                "drivers_per_robot": case["calendar"]["drivers_per_robot"],
            }
            merge(
                cur,
                prefix + ".BOM",
                [
                    "COMPANY",
                    "SCOPE",
                    "SCHEMA_VERSION",
                    "SNAPSHOT_ID",
                    "SOURCE_ID",
                    "ORDINAL",
                    "PAYLOAD",
                    "SHA256",
                    "AVAILABLE_AT",
                ],
                [
                    company,
                    scope,
                    SCHEMA_VERSION,
                    snapshot_id,
                    "BOM-DRV-24-B",
                    0,
                    json.dumps(bom),
                    fingerprint(bom),
                    at,
                ],
                ["COMPANY", "SCOPE", "SCHEMA_VERSION", "SNAPSHOT_ID", "SOURCE_ID"],
            )
            for candidate, claims in case["confirmations"].items():
                merge(
                    cur,
                    prefix + ".RESERVATIONS",
                    [
                        "COMPANY",
                        "SCOPE",
                        "SCHEMA_VERSION",
                        "SNAPSHOT_ID",
                        "SOURCE_ID",
                        "ORDINAL",
                        "PAYLOAD",
                        "SHA256",
                        "AVAILABLE_AT",
                    ],
                    [
                        company,
                        scope,
                        SCHEMA_VERSION,
                        snapshot_id,
                        candidate,
                        0,
                        json.dumps(claims),
                        fingerprint(claims),
                        at,
                    ],
                    ["COMPANY", "SCOPE", "SCHEMA_VERSION", "SNAPSHOT_ID", "SOURCE_ID"],
                )
            for d in bundle["documents"]:
                meta = bundle["metadata"][d.id]
                merge(
                    cur,
                    prefix + ".EVIDENCE_DOCUMENTS",
                    [
                        "DOCUMENT_ID",
                        "COMPANY",
                        "SCOPE",
                        "SCHEMA_VERSION",
                        "TEXT",
                        "SHA256",
                        "AVAILABLE_AT",
                        "SOURCE_KIND",
                        "SOURCE_REFERENCE",
                        "TRUSTED",
                        "CLAIMS",
                    ],
                    [
                        d.id,
                        company,
                        scope,
                        SCHEMA_VERSION,
                        d.text,
                        d.sha256,
                        d.available_at,
                        d.source_kind,
                        meta["reference"],
                        True,
                        json.dumps(meta["claims"]),
                    ],
                    ["DOCUMENT_ID", "COMPANY", "SCOPE", "SCHEMA_VERSION", "SHA256"],
                )
                merge(
                    cur,
                    prefix + ".SNAPSHOT_MEMBERS",
                    [
                        "SNAPSHOT_ID",
                        "COMPANY",
                        "SCOPE",
                        "SCHEMA_VERSION",
                        "DOCUMENT_ID",
                        "SOURCE_SHA256",
                    ],
                    [snapshot_id, company, scope, SCHEMA_VERSION, d.id, d.sha256],
                    [
                        "SNAPSHOT_ID",
                        "COMPANY",
                        "SCOPE",
                        "SCHEMA_VERSION",
                        "DOCUMENT_ID",
                    ],
                )
                if meta["claims"]:
                    merge(
                        cur,
                        prefix + ".EXTRACTED_CLAIMS",
                        [
                            "CLAIM_ID",
                            "DOCUMENT_ID",
                            "SOURCE_SHA256",
                            "COMPANY",
                            "SCOPE",
                            "SCHEMA_VERSION",
                            "AVAILABLE_AT",
                            "POLICY_VERSION",
                            "CLAIMS",
                        ],
                        [
                            fingerprint(meta["claims"]),
                            d.id,
                            d.sha256,
                            company,
                            scope,
                            SCHEMA_VERSION,
                            d.available_at,
                            POLICY_VERSION,
                            json.dumps(meta["claims"]),
                        ],
                        [
                            "CLAIM_ID",
                            "DOCUMENT_ID",
                            "SOURCE_SHA256",
                            "COMPANY",
                            "SCOPE",
                            "SCHEMA_VERSION",
                        ],
                    )
            header = {
                k: v
                for k, v in case.items()
                if k
                not in {field for field, _ in TABLE_FIELDS.values()} | {"initial_documents", "confirmations"}
            }
            merge(
                cur,
                prefix + ".SNAPSHOTS",
                [
                    "SNAPSHOT_ID",
                    "COMPANY",
                    "SCOPE",
                    "SCHEMA_VERSION",
                    "POLICY_VERSION",
                    "AS_OF",
                    "HEADER",
                    "CASE_HASH",
                    "FINGERPRINT",
                    "DOCUMENT_COUNT",
                    "STATUS",
                ],
                [
                    snapshot_id,
                    company,
                    scope,
                    SCHEMA_VERSION,
                    POLICY_VERSION,
                    at,
                    json.dumps(header),
                    fingerprint(case),
                    bundle["fingerprint"],
                    len(bundle["documents"]),
                    "PUBLISHED",
                ],
                ["SNAPSHOT_ID", "COMPANY", "SCOPE", "SCHEMA_VERSION"],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-as-of",
        help="Explicitly publish trusted supplier confirmations released by this scenario time",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    bundle = prepare(args.release_as_of)
    if args.apply:
        publish(bundle, get_settings())
    print(
        json.dumps(
            {
                "mode": "published" if args.apply else "local_validation_only",
                "snapshot_id": bundle["id"],
                "as_of": bundle["as_of"],
                "documents": len(bundle["documents"]),
                "confirmations": len(bundle["case"]["confirmations"]),
                "fingerprint": bundle["fingerprint"],
            }
        )
    )


if __name__ == "__main__":
    main()
