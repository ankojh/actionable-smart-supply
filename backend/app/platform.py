"""Snowflake operational evidence and hosted inference; never a fixture fallback.

All SQL is application-owned. Search is an untrusted retrieval hint until its rows
resolve to the exact SQL snapshot. Analyst SQL is a review artifact, never executed.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlparse
from urllib.request import Request

from .config import Settings, get_settings
from .domain import SCHEMA_VERSION, Document, EvidenceSnapshot, fingerprint
from .snowflake_client import SnowflakeError, connection as connector_connection, query as connector_query

TABLE_FIELDS = {
    "INVENTORY": ("inventory_on_hand", True),
    "ALLOCATIONS": ("allocation_ledger", True),
    "CUSTOMER_ORDERS": ("production_orders", True),
    "INBOUND_POS": ("primary_po", False),
    "RECOVERY_STOCK": ("internal_transfer", False),
    "SUPPLIER_OFFERS": ("candidate_offers", True),
    "PRODUCTION_CAPACITY": ("calendar", False),
    "POLICIES": ("policies", False),
}


class PlatformError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str = "Snowflake operation failed. Check service access and backend configuration.",
    ):
        self.code = code
        self.safe_message = message
        super().__init__(message)


def review_sql(statement: str) -> str:
    """Validate an investigative display artifact; never execute generated SQL.

    Comments and quoted text are lexed separately so ordinary Analyst comments
    and strings do not look like additional executable statements.
    """
    pattern = r"('(?:''|[^'])*')|(\"(?:\"\"|[^\"])*\")|(--[^\n]*|/\*[\s\S]*?\*/)"

    def strip_comment(match):
        return " " if match.group(3) else match.group(0)

    display = re.sub(pattern, strip_comment, statement).strip()
    if display.endswith(";"):
        display = display[:-1].rstrip()
    check = re.sub(
        pattern,
        lambda match: " " if match.group(1) or match.group(2) or match.group(3) else match.group(0),
        display,
    )
    if not re.match(r"(?is)^(SELECT|WITH)\b", check) or re.search(
        r"(?i);|\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|CALL|COPY|PUT|GET|GRANT|REVOKE|EXECUTE|SYSTEM\$)\b",
        check,
    ):
        raise PlatformError(
            "ANALYST_SQL_REJECTED",
            "Analyst returned a statement outside the read-only investigative boundary.",
        )
    return display


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise PlatformError(
            "INVALID_CONFIGURATION", "Snowflake identifiers must be ordinary unquoted identifiers."
        )
    return value.upper()


def instant(value: Any) -> datetime:
    try:
        dt = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise PlatformError("INVALID_TIME", "An ISO-8601 evidence time is required.") from None


def time_text(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def decoded(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        raise PlatformError("INVALID_PROVIDER_RESPONSE") from None


def snapshot_fingerprint(case: dict, documents: list[Document] | tuple[Document, ...]) -> str:
    return fingerprint(
        {
            "case": case,
            "documents": [d.model_dump(mode="json") for d in sorted(documents, key=lambda d: d.id)],
        }
    )


class SnowflakePlatform:
    """Dedicated single-company synthetic schema, injected seams for connector tests."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        query_fn: Callable | None = None,
        rest_fn: Callable | None = None,
    ):
        source = settings or get_settings()
        self.settings = source.model_copy(update={"role": source.app_role, "schema_": source.app_schema})
        self.database = identifier(source.database or "")
        self.schema = identifier(source.app_schema)
        self.prefix = f"{self.database}.{self.schema}"
        self.search_service = identifier(source.search_service)
        self.semantic_view = identifier(source.semantic_view)
        self._query_fn = query_fn
        self._rest_fn = rest_fn
        self._usage = threading.local()

    @property
    def native_agents(self):
        return self.settings.use_cortex_agents

    def create_agent_thread(self):
        result = self._rest("/api/v2/cortex/threads", {"origin_application": "sumo"})
        thread_id = str(result.get("thread_id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", thread_id):
            raise PlatformError("INVALID_AGENT_THREAD")
        # Keep Snowflake's 64-bit identifiers as strings for the browser.
        return thread_id

    def run_agent(self, role, thread_id, parent_message_id, content):
        from .cortex import AGENT_NAMES

        if role not in AGENT_NAMES or not re.fullmatch(r"[1-9][0-9]*", str(thread_id)):
            raise PlatformError("INVALID_AGENT_REQUEST")
        if not re.fullmatch(r"[0-9]+", str(parent_message_id)) or not isinstance(content, list):
            raise PlatformError("INVALID_AGENT_REQUEST")
        self._usage.request_id = None
        result = self._rest(
            f"/api/v2/databases/{self.database}/schemas/{self.schema}/agents/{AGENT_NAMES[role]}:run",
            {
                "thread_id": int(thread_id), "parent_message_id": int(parent_message_id),
                "messages": [{"role": "user", "content": content}], "stream": False,
            },
        )
        if not isinstance(result.get("content"), list) or result.get("error") or result.get("warnings"):
            raise PlatformError("INVALID_AGENT_RESPONSE", "The managed Snowflake Agent did not return a complete usable response.")
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            raise PlatformError("INVALID_AGENT_RESPONSE")
        message_id = str(metadata.get("assistant_message_id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", message_id):
            raise PlatformError("INVALID_AGENT_RESPONSE")
        if str(metadata.get("thread_id", thread_id)) != str(thread_id):
            raise PlatformError("INVALID_AGENT_RESPONSE")
        metadata = {**metadata, "thread_id": str(thread_id), "assistant_message_id": message_id}
        if getattr(self._usage, "request_id", None):
            metadata["request_id"] = self._usage.request_id
        return {"content": result["content"], "metadata": metadata}

    @property
    def last_usage(self) -> dict[str, Any] | None:
        return getattr(self._usage, "value", None)

    @last_usage.setter
    def last_usage(self, value: dict[str, Any] | None) -> None:
        self._usage.value = value

    def _sql(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        try:
            rows = (
                self._query_fn(sql, params)
                if self._query_fn
                else connector_query(sql, params, settings=self.settings)
            )
            if len(rows) > self.settings.max_query_rows:
                raise PlatformError("ROW_LIMIT")
            return [{str(k).upper(): v for k, v in row.items()} for row in rows]
        except PlatformError:
            raise
        except SnowflakeError as error:
            raise PlatformError(error.code) from None
        except Exception:
            raise PlatformError("SQL_UNAVAILABLE") from None

    def _scope(self, company: str, scope: str) -> None:
        if company != self.settings.company or scope != self.settings.scope:
            raise PlatformError(
                "SCOPE_DENIED", "The requested company or scenario is outside this deployment's scope."
            )

    def _rest(self, path: str, body: dict, *, method: str = "POST") -> dict:
        if self._rest_fn:
            try:
                result = self._rest_fn(path, body)
                if not isinstance(result, dict):
                    raise PlatformError("INVALID_PROVIDER_RESPONSE")
                return result
            except PlatformError:
                raise
            except Exception:
                raise PlatformError("CORTEX_UNAVAILABLE") from None
        if method not in {"GET", "POST"} or not path.startswith("/api/v2/"):
            raise PlatformError("INVALID_REST_REQUEST")
        # Choose the configured route before I/O. There is no auth fallback.
        if self.settings.rest_auth == "session":
            failure = None
            result = None
            try:
                with connector_connection(self.settings) as conn:
                    # The official Cortex SDK authentication example uses these
                    # public connector properties to obtain its SQL session token.
                    rest = getattr(conn, "rest", None)
                    token = getattr(rest, "token", None)
                    if not isinstance(token, str) or not token or any(char in token for char in '\r\n"'):
                        failure = PlatformError(
                            "REST_SESSION_AUTH_REQUIRED",
                            "The application-role SQL connection did not yield a usable session token.",
                        )
                    else:
                        try:
                            result = self._rest_request(path, body, f'Snowflake Token="{token}"', method)
                        except PlatformError as error:
                            # Keep provider failures outside the SQL connection
                            # manager so they retain their safe REST error codes.
                            failure = error
            except SnowflakeError as error:
                raise PlatformError(
                    error.code, "The application-role Snowflake session could not be opened."
                ) from None
            if failure:
                raise failure from None
            return result
        if not self.settings.token:
            raise PlatformError(
                "REST_AUTH_REQUIRED",
                "Cortex REST PAT authentication requires a configured programmatic access token.",
            )
        # A SQL password is never repurposed as a bearer token.
        return self._rest_request(path, body, "Bearer " + self.settings.token, method)

    def _rest_request(self, path: str, body: dict, authorization: str, method: str) -> dict:
        account = self.settings.account
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", account):
            raise PlatformError("INVALID_CONFIGURATION")
        base = (
            self.settings.rest_base_url
            or f"https://{account.replace(chr(95), chr(45))}.snowflakecomputing.com"
        )
        parsed = urlparse(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".snowflakecomputing.com")
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise PlatformError("INVALID_CONFIGURATION")
        req = Request(
            base.rstrip("/") + path,
            data=json.dumps(body).encode() if method == "POST" else None,
            method=method,
            headers={
                "Authorization": authorization,
                "X-Snowflake-Role": self.settings.app_role,
                "X-Snowflake-Warehouse": self.settings.warehouse or "",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            # Do not follow redirects with credentials to an unexpected host.
            from urllib.request import HTTPRedirectHandler, build_opener

            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            with build_opener(NoRedirect()).open(
                req, timeout=90 if "/agents/" in path and path.endswith(":run") else self.settings.network_timeout_seconds
            ) as response:
                self._usage.request_id = getattr(response, "headers", {}).get("X-Snowflake-Request-ID")
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise PlatformError("RESPONSE_LIMIT")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise PlatformError("INVALID_PROVIDER_RESPONSE")
                return result
        except PlatformError:
            raise
        except __import__("urllib.error", fromlist=["HTTPError"]).HTTPError as error:
            raise PlatformError(
                f"CORTEX_HTTP_{error.code}",
                "Cortex REST request failed. Check authentication, role, service readiness and request shape.",
            ) from None
        except Exception:
            raise PlatformError("CORTEX_UNAVAILABLE") from None

    def _documents(self, snapshot_id: str, company: str, scope: str, as_of: str) -> tuple[Document, ...]:
        rows = self._sql(
            f"""SELECT D.DOCUMENT_ID,D.TEXT,D.SHA256,D.AVAILABLE_AT,D.COMPANY,D.SCOPE,
                 D.SCHEMA_VERSION,D.SOURCE_KIND,M.SOURCE_SHA256
            FROM {self.prefix}.SNAPSHOT_MEMBERS M JOIN {self.prefix}.EVIDENCE_DOCUMENTS D
              ON D.COMPANY=M.COMPANY AND D.SCOPE=M.SCOPE AND D.SCHEMA_VERSION=M.SCHEMA_VERSION
             AND D.DOCUMENT_ID=M.DOCUMENT_ID AND D.SHA256=M.SOURCE_SHA256
            WHERE M.SNAPSHOT_ID=%s AND M.COMPANY=%s AND M.SCOPE=%s AND M.SCHEMA_VERSION=%s
              AND D.AVAILABLE_AT<=TO_TIMESTAMP_NTZ(%s) ORDER BY D.DOCUMENT_ID LIMIT 501""",
            (snapshot_id, company, scope, SCHEMA_VERSION, as_of),
        )
        docs = []
        for row in rows:
            document = Document(
                id=row["DOCUMENT_ID"],
                text=row["TEXT"],
                sha256=row["SHA256"],
                available_at=time_text(row["AVAILABLE_AT"]),
                company=row["COMPANY"],
                scope=row["SCOPE"],
                version=row["SCHEMA_VERSION"],
                source_kind=row["SOURCE_KIND"],
            )
            if (
                document.company != company
                or document.scope != scope
                or document.version != SCHEMA_VERSION
                or document.sha256 != row["SOURCE_SHA256"]
                or hashlib.sha256(document.text.encode()).hexdigest() != document.sha256
                or instant(document.available_at) > instant(as_of)
            ):
                raise PlatformError(
                    "EVIDENCE_INTEGRITY", "Evidence failed snapshot scope, availability or hash validation."
                )
            docs.append(document)
        if len({d.id for d in docs}) != len(docs):
            raise PlatformError("EVIDENCE_INTEGRITY")
        return tuple(docs)

    def check_search_ready(self):
        rows = self._sql(f"DESCRIBE CORTEX SEARCH SERVICE {self.prefix}.{self.search_service}")
        # DESCRIBE reports ACTIVE on the live service; some accounts return RUNNING.
        if len(rows) != 1 or str(rows[0].get("SERVING_STATE", "")).upper() not in {"ACTIVE", "RUNNING"}:
            raise PlatformError(
                "SEARCH_PAUSED",
                "The evidence search service is paused or unavailable. Resume it before starting a live assessment.",
            )

    def snapshot_head(self, company: str, scope: str) -> dict:
        """Version metadata only, so stale detection does not expose future claims."""
        self._scope(company, scope)
        rows = self._sql(
            f"SELECT SNAPSHOT_ID,AS_OF FROM {self.prefix}.SNAPSHOTS WHERE COMPANY=%s AND SCOPE=%s AND SCHEMA_VERSION=%s AND STATUS='PUBLISHED' ORDER BY AS_OF DESC,SNAPSHOT_ID DESC LIMIT 1",
            (company, scope, SCHEMA_VERSION),
        )
        if not rows:
            raise PlatformError("SNAPSHOT_NOT_FOUND")
        return {"id": rows[0]["SNAPSHOT_ID"], "as_of": time_text(rows[0]["AS_OF"])}

    def load_snapshot(self, company: str, scope: str, as_of: str) -> EvidenceSnapshot:
        self._scope(company, scope)
        instant(as_of)
        manifests = self._sql(
            f"""SELECT SNAPSHOT_ID,AS_OF,HEADER,POLICY_VERSION,CASE_HASH,FINGERPRINT,DOCUMENT_COUNT
            FROM {self.prefix}.SNAPSHOTS WHERE COMPANY=%s AND SCOPE=%s AND SCHEMA_VERSION=%s
            AND AS_OF<=TO_TIMESTAMP_NTZ(%s) AND STATUS='PUBLISHED' ORDER BY AS_OF DESC,SNAPSHOT_ID DESC LIMIT 1""",
            (company, scope, SCHEMA_VERSION, as_of),
        )
        if not manifests:
            raise PlatformError(
                "SNAPSHOT_NOT_FOUND",
                "No published Snowflake evidence snapshot is available at this scenario time.",
            )
        manifest = manifests[0]
        snapshot_id = manifest["SNAPSHOT_ID"]
        snapshot_time = time_text(manifest["AS_OF"])
        if instant(snapshot_time) > instant(as_of):
            raise PlatformError("EVIDENCE_INTEGRITY")
        case = decoded(manifest["HEADER"])
        if not isinstance(case, dict):
            raise PlatformError("EVIDENCE_INTEGRITY")
        case = dict(case)
        for table, (field, many) in TABLE_FIELDS.items():
            rows = self._sql(
                f"""SELECT SOURCE_ID,PAYLOAD,SHA256,AVAILABLE_AT FROM {self.prefix}.V_{table}
                WHERE COMPANY=%s AND SCOPE=%s AND SCHEMA_VERSION=%s AND SNAPSHOT_ID=%s
                  AND AVAILABLE_AT<=TO_TIMESTAMP_NTZ(%s) ORDER BY ORDINAL,SOURCE_ID LIMIT 501""",
                (company, scope, SCHEMA_VERSION, snapshot_id, snapshot_time),
            )
            values = []
            for row in rows:
                value = decoded(row["PAYLOAD"])
                if (
                    not isinstance(value, dict)
                    or fingerprint(value) != row["SHA256"]
                    or instant(row["AVAILABLE_AT"]) > instant(snapshot_time)
                ):
                    raise PlatformError("EVIDENCE_INTEGRITY")
                values.append(value)
            if not many and len(values) != 1:
                raise PlatformError(
                    "EVIDENCE_INCOMPLETE", "A required operational source is missing or duplicated."
                )
            if many and not values:
                raise PlatformError("EVIDENCE_INCOMPLETE")
            case[field] = values if many else values[0]
        # BOM is separately governed rather than inferred from a model response.
        bom = self._sql(
            f"""SELECT PAYLOAD,SHA256 FROM {self.prefix}.V_BOM WHERE COMPANY=%s AND SCOPE=%s
            AND SCHEMA_VERSION=%s AND SNAPSHOT_ID=%s AND AVAILABLE_AT<=TO_TIMESTAMP_NTZ(%s) LIMIT 2""",
            (company, scope, SCHEMA_VERSION, snapshot_id, snapshot_time),
        )
        if (
            len(bom) != 1
            or fingerprint(decoded(bom[0]["PAYLOAD"])) != bom[0]["SHA256"]
            or decoded(bom[0]["PAYLOAD"]).get("drivers_per_robot") != case["calendar"]["drivers_per_robot"]
        ):
            raise PlatformError("EVIDENCE_INTEGRITY")
        documents = self._documents(snapshot_id, company, scope, snapshot_time)
        if len(documents) != manifest["DOCUMENT_COUNT"]:
            raise PlatformError("EVIDENCE_INCOMPLETE")
        case["initial_documents"] = [
            {"id": d.id, "text": d.text} for d in documents if d.source_kind == "synthetic_source"
        ]
        reservations = self._sql(
            f"""SELECT SOURCE_ID,PAYLOAD,SHA256 FROM {self.prefix}.V_RESERVATIONS
            WHERE COMPANY=%s AND SCOPE=%s AND SCHEMA_VERSION=%s AND SNAPSHOT_ID=%s
              AND AVAILABLE_AT<=TO_TIMESTAMP_NTZ(%s) ORDER BY SOURCE_ID LIMIT 501""",
            (company, scope, SCHEMA_VERSION, snapshot_id, snapshot_time),
        )
        case["confirmations"] = {}
        for row in reservations:
            claims = decoded(row["PAYLOAD"])
            if (
                not isinstance(claims, dict)
                or fingerprint(claims) != row["SHA256"]
                or row["SOURCE_ID"] in case["confirmations"]
            ):
                raise PlatformError("EVIDENCE_INTEGRITY")
            case["confirmations"][row["SOURCE_ID"]] = claims
        if (
            fingerprint(case) != manifest["CASE_HASH"]
            or snapshot_fingerprint(case, documents) != manifest["FINGERPRINT"]
        ):
            raise PlatformError(
                "EVIDENCE_INTEGRITY",
                "The published snapshot manifest does not match its operational evidence.",
            )
        return EvidenceSnapshot(
            id=snapshot_id,
            company=company,
            scope=scope,
            policy_version=manifest["POLICY_VERSION"],
            as_of=snapshot_time,
            fingerprint=manifest["FINGERPRINT"],
            case=case,
            documents=documents,
        )

    def search(self, snapshot: EvidenceSnapshot, query: str) -> list[Document]:
        self._scope(snapshot.company, snapshot.scope)
        if snapshot.schema_version != SCHEMA_VERSION or not query.strip() or len(query) > 2000:
            raise PlatformError("INVALID_SEARCH")
        # Re-read SQL membership, never rely on caller-provided document metadata.
        authoritative = self._documents(snapshot.id, snapshot.company, snapshot.scope, snapshot.as_of)
        members = {doc.id: doc for doc in authoritative}
        if {d.id: d.sha256 for d in snapshot.documents} != {d.id: d.sha256 for d in authoritative}:
            raise PlatformError("STALE_SNAPSHOT")
        body = {
            "query": query,
            "columns": [
                "DOCUMENT_ID",
                "SHA256",
                "COMPANY",
                "SCOPE",
                "SCHEMA_VERSION",
                "AVAILABLE_AT",
                "SNAPSHOT_ID",
            ],
            "limit": 10,
            "filter": {
                "@and": [
                    {"@eq": {"COMPANY": snapshot.company}},
                    {"@eq": {"SCOPE": snapshot.scope}},
                    {"@eq": {"SCHEMA_VERSION": snapshot.schema_version}},
                    {"@eq": {"SNAPSHOT_ID": snapshot.id}},
                    {"@lte": {"AVAILABLE_AT": instant(snapshot.as_of).isoformat()}},
                ]
            },
        }
        path = f"/api/v2/databases/{quote(self.database)}/schemas/{quote(self.schema)}/cortex-search-services/{quote(self.search_service)}:query"
        results = self._rest(path, body).get("results")
        if not isinstance(results, list) or len(results) > 10:
            raise PlatformError("INVALID_SEARCH_RESPONSE")
        found = []
        for result in results:
            if not isinstance(result, dict):
                raise PlatformError("INVALID_SEARCH_RESPONSE")
            hit = {str(k).upper(): v for k, v in result.items()}
            doc = members.get(hit.get("DOCUMENT_ID"))
            if (
                not doc
                or hit.get("SHA256") != doc.sha256
                or hit.get("COMPANY") != snapshot.company
                or hit.get("SCOPE") != snapshot.scope
                or hit.get("SCHEMA_VERSION") != snapshot.schema_version
                or hit.get("SNAPSHOT_ID") != snapshot.id
                or instant(hit.get("AVAILABLE_AT")) != instant(doc.available_at)
            ):
                raise PlatformError(
                    "SEARCH_CITATION_REJECTED", "Search returned evidence outside the authoritative snapshot."
                )
            if doc.id not in {d.id for d in found}:
                found.append(doc)
        return found

    @property
    def last_raw_completion(self):
        return getattr(self._usage, "raw_completion", None)

    @property
    def last_inference_diagnostic(self):
        return getattr(self._usage, "diagnostic", None)

    def complete(self, role: str, prompt: str, response_schema: dict) -> dict:
        self.last_usage = None
        self._usage.diagnostic = None
        self._usage.raw_completion = None
        if (
            role not in {"operations", "sourcing", "qualification", "single"}
            or not prompt
            or len(prompt) > 120000
        ):
            raise PlatformError("INVALID_INFERENCE_REQUEST")
        if not self.settings.cortex_model.startswith("claude-") or response_schema.get("type") != "object":
            raise PlatformError("INVALID_INFERENCE_CONFIGURATION")
        # Named arguments and PARSE_JSON ensure schema is a SQL OBJECT, not text.
        rows = self._sql(
            """SELECT AI_COMPLETE(model => %s,prompt => %s,
            model_parameters => PARSE_JSON(%s),response_format => PARSE_JSON(%s),show_details => TRUE) AS RESULT""",
            (
                self.settings.cortex_model,
                prompt,
                json.dumps({"temperature": 0, "max_tokens": self.settings.max_completion_tokens}),
                json.dumps({"type": "json", "schema": response_schema}),
            ),
        )
        if len(rows) != 1:
            raise PlatformError("INVALID_INFERENCE_RESPONSE")
        self._usage.raw_completion = rows[0].get("RESULT")
        response = decoded(rows[0].get("RESULT"))
        if isinstance(response, dict):
            self.last_usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
            structured = response.get("structured_output")
            self._usage.diagnostic = {
                "response_keys": sorted(response),
                "structured_type": type(structured).__name__,
                "blocks": len(structured) if isinstance(structured, list) else None,
                "raw_message_types": [
                    type(block.get("raw_message")).__name__ for block in structured if isinstance(block, dict)
                ]
                if isinstance(structured, list)
                else None,
            }
        if not isinstance(response, dict):
            raise PlatformError("INVALID_INFERENCE_RESPONSE")
        structured = response.get("structured_output")
        # Snowflake's JSON-schema example returns one typed raw_message block.
        # Its Returns section also documents structured_output as an object.
        # Accept only those two shapes, not an arbitrary block/text conversion.
        if isinstance(structured, list):
            if (
                len(structured) != 1
                or not isinstance(structured[0], dict)
                or structured[0].get("type") != "json"
                or not isinstance(structured[0].get("raw_message"), dict)
            ):
                raise PlatformError("INVALID_INFERENCE_RESPONSE")
            result = structured[0]["raw_message"]
        elif isinstance(structured, dict):
            result = structured
        else:
            raise PlatformError("INVALID_INFERENCE_RESPONSE")
        self.last_usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
        # Full typed validation is the role owner's responsibility; reject shape
        # errors here as well so no provider output is silently repaired.
        try:
            from jsonschema import validate

            validate(result, response_schema)
        except Exception:
            raise PlatformError(
                "INVALID_INFERENCE_RESPONSE", "Hosted Claude returned an invalid specialist contract."
            ) from None
        return result

    def analyst(self, question: str, snapshot: EvidenceSnapshot) -> dict:
        self._scope(snapshot.company, snapshot.scope)
        if not question.strip() or len(question) > 2000:
            raise PlatformError("INVALID_ANALYST_REQUEST")
        scoped_question = f"For company {snapshot.company}, scope {snapshot.scope}, snapshot {snapshot.id}, schema {snapshot.schema_version}, as of {snapshot.as_of}: {question}\nRead-only operational investigation. Always filter those exact dimensions. No purchasing or approval recommendations."
        result = self._rest(
            "/api/v2/cortex/analyst/message",
            {
                "messages": [{"role": "user", "content": [{"type": "text", "text": scoped_question}]}],
                "semantic_view": f"{self.prefix}.{self.semantic_view}",
                "stream": False,
            },
        )
        message = result.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            raise PlatformError("INVALID_ANALYST_RESPONSE")
        blocks = message["content"]
        safe_blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                raise PlatformError("INVALID_ANALYST_RESPONSE")
            if block.get("type") == "sql":
                statement = review_sql(block.get("statement", ""))
                safe_blocks.append(
                    {"type": "sql", "statement": statement, "execution_status": "NOT_EXECUTED_REVIEW_ONLY"}
                )
            elif block.get("type") in {"text", "suggestions"}:
                safe_blocks.append(block)
        return {
            "snapshot_id": snapshot.id,
            "content": safe_blocks,
            "request_id": result.get("request_id"),
            "approval_authority": False,
            "note": "Analyst SQL is for investigative review; deterministic snapshot tools control all recovery and approval arithmetic.",
        }

    def append_records(self, records: list[dict]) -> None:
        if len(records) > 100:
            raise PlatformError("RECORD_LIMIT")
        for record in records:
            required = {
                "event_id",
                "run_id",
                "kind",
                "company",
                "scope",
                "snapshot_id",
                "policy_version",
                "payload",
                "created_at",
            }
            if not required.issubset(record) or not record["event_id"] or len(json.dumps(record)) > 100000:
                raise PlatformError("INVALID_AUDIT_RECORD")
            self._scope(record["company"], record["scope"])
            if record["kind"].lower() in {
                "outcome",
                "outcome_report",
                "evaluation",
                "evaluation_result",
                "realized_event",
                "oracle",
            }:
                raise PlatformError("EVALUATOR_ROLE_REQUIRED")
            value_hash = fingerprint(record)
            prior = self._sql(
                f"SELECT RECORD_HASH FROM {self.prefix}.AUDIT_RECORDS WHERE EVENT_ID=%s",
                (record["event_id"],),
            )
            if prior:
                if len(prior) != 1 or prior[0]["RECORD_HASH"] != value_hash:
                    raise PlatformError("IDEMPOTENCY_CONFLICT")
                continue
            self._sql(
                f"""MERGE INTO {self.prefix}.AUDIT_RECORDS T USING (SELECT %s AS EVENT_ID,%s AS RECORD_HASH,PARSE_JSON(%s) AS RECORD) S
                ON T.EVENT_ID=S.EVENT_ID WHEN NOT MATCHED THEN INSERT (EVENT_ID,RECORD_HASH,RECORD,CREATED_AT)
                VALUES(S.EVENT_ID,S.RECORD_HASH,S.RECORD,CURRENT_TIMESTAMP())""",
                (record["event_id"], value_hash, json.dumps(record)),
            )

    def append_evaluation_records(self, records: list[dict]) -> None:
        """Explicit evaluator-role export; these tables are never planner-readable."""
        if not self._query_fn and not self.settings.evaluator_token:
            raise PlatformError(
                "EVALUATOR_AUTH_REQUIRED",
                "Evaluation export requires its separately configured evaluator token.",
            )
        evaluator = self.settings.model_copy(
            update={
                "role": self.settings.evaluator_role,
                "user": self.settings.evaluator_user or self.settings.user,
                "auth": "pat",
                "token": self.settings.evaluator_token,
            }
        )
        if len(records) > 100:
            raise PlatformError("RECORD_LIMIT")
        for record in records:
            if not {"event_id", "run_id", "company", "scope", "payload"}.issubset(record):
                raise PlatformError("INVALID_AUDIT_RECORD")
            self._scope(record["company"], record["scope"])
            record_hash = fingerprint(record)
            select = (
                f"SELECT RECORD_HASH FROM {self.database}.{self.schema}_EVAL.RESULT_RECORDS WHERE EVENT_ID=%s"
            )
            try:
                prior = (
                    self._query_fn(select, (record["event_id"],))
                    if self._query_fn
                    else connector_query(select, (record["event_id"],), settings=evaluator)
                )
            except Exception:
                raise PlatformError("EVALUATION_EXPORT_UNAVAILABLE") from None
            if prior:
                if len(prior) != 1 or prior[0].get("RECORD_HASH") != record_hash:
                    raise PlatformError("IDEMPOTENCY_CONFLICT")
                continue
            sql = f"""MERGE INTO {self.database}.{self.schema}_EVAL.RESULT_RECORDS T
                USING (SELECT %s AS EVENT_ID,%s AS RECORD_HASH,PARSE_JSON(%s) AS RECORD) S
                ON T.EVENT_ID=S.EVENT_ID WHEN NOT MATCHED THEN INSERT (EVENT_ID,RECORD_HASH,RECORD,CREATED_AT)
                VALUES(S.EVENT_ID,S.RECORD_HASH,S.RECORD,CURRENT_TIMESTAMP())"""
            try:
                if self._query_fn:
                    self._query_fn(sql, (record["event_id"], fingerprint(record), json.dumps(record)))
                else:
                    connector_query(
                        sql, (record["event_id"], fingerprint(record), json.dumps(record)), settings=evaluator
                    )
            except Exception:
                raise PlatformError("EVALUATION_EXPORT_UNAVAILABLE") from None

    def ingest_confirmation(
        self, snapshot: EvidenceSnapshot, document: Document, claims: dict, source_reference: str
    ) -> EvidenceSnapshot:
        self._scope(snapshot.company, snapshot.scope)
        if (
            document.source_kind != "supplier_confirmation"
            or document.company != snapshot.company
            or document.scope != snapshot.scope
            or document.version != snapshot.schema_version
        ):
            raise PlatformError("UNTRUSTED_CONFIRMATION")
        rows = self._sql(
            f"""SELECT D.TEXT,D.SHA256,D.AVAILABLE_AT,D.CLAIMS,D.SOURCE_REFERENCE,S.AS_OF
            FROM {self.prefix}.EVIDENCE_DOCUMENTS D JOIN {self.prefix}.SNAPSHOT_MEMBERS M
              ON D.DOCUMENT_ID=M.DOCUMENT_ID AND D.SHA256=M.SOURCE_SHA256 AND D.COMPANY=M.COMPANY AND D.SCOPE=M.SCOPE AND D.SCHEMA_VERSION=M.SCHEMA_VERSION
            JOIN {self.prefix}.SNAPSHOTS S ON S.SNAPSHOT_ID=M.SNAPSHOT_ID AND S.COMPANY=M.COMPANY AND S.SCOPE=M.SCOPE AND S.SCHEMA_VERSION=M.SCHEMA_VERSION
            WHERE D.COMPANY=%s AND D.SCOPE=%s AND D.SCHEMA_VERSION=%s AND D.DOCUMENT_ID=%s
              AND D.TRUSTED=TRUE AND D.SOURCE_KIND='supplier_confirmation' AND S.STATUS='PUBLISHED'
              AND D.AVAILABLE_AT<=S.AS_OF AND S.AS_OF>TO_TIMESTAMP_NTZ(%s) ORDER BY S.AS_OF LIMIT 1""",
            (snapshot.company, snapshot.scope, snapshot.schema_version, document.id, snapshot.as_of),
        )
        if not rows:
            raise PlatformError(
                "UNTRUSTED_CONFIRMATION",
                "No authoritative published supplier confirmation matches this reply.",
            )
        row = rows[0]
        if (
            document.text != row["TEXT"]
            or document.sha256 != row["SHA256"]
            or hashlib.sha256(document.text.encode()).hexdigest() != row["SHA256"]
            or instant(document.available_at) != instant(row["AVAILABLE_AT"])
            or claims != decoded(row["CLAIMS"])
            or source_reference != row["SOURCE_REFERENCE"]
        ):
            raise PlatformError(
                "CONFIRMATION_MISMATCH", "The reply does not match the trusted source document and claims."
            )
        successor = self.load_snapshot(snapshot.company, snapshot.scope, time_text(row["AS_OF"]))
        if not {d.id for d in snapshot.documents}.issubset({d.id for d in successor.documents}) or not any(
            d.id == document.id and d.sha256 == document.sha256 for d in successor.documents
        ):
            raise PlatformError("EVIDENCE_INTEGRITY")
        return successor
