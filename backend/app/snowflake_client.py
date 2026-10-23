"""Bounded connector seam. Provider messages never cross this boundary."""

from contextlib import contextmanager
from typing import Any, Iterator

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from .config import Settings, get_settings


class SnowflakeError(RuntimeError):
    def __init__(self, code: str = "SNOWFLAKE_UNAVAILABLE"):
        self.code = code
        super().__init__("Snowflake service unavailable. Check backend configuration and service access.")


def _connect_args(s: Settings) -> dict[str, Any]:
    args: dict[str, Any] = {
        "account": s.account,
        "user": s.user,
        "role": s.role,
        "warehouse": s.warehouse,
        "database": s.database,
        "schema": s.schema_,
        "application": "sumo",
        "login_timeout": s.network_timeout_seconds,
        "network_timeout": s.network_timeout_seconds,
        "session_parameters": {
            "QUERY_TAG": "project:recovery-v1:backend",
            "STATEMENT_TIMEOUT_IN_SECONDS": s.statement_timeout_seconds,
            "ABORT_DETACHED_QUERY": True,
        },
    }
    if s.auth == "password":
        if not s.password:
            raise SnowflakeError("SNOWFLAKE_CONFIGURATION")
        args["password"] = s.password
    elif s.auth == "keypair":
        if not s.private_key_path:
            raise SnowflakeError("SNOWFLAKE_CONFIGURATION")
        with open(s.private_key_path, "rb") as f:
            key = serialization.load_pem_private_key(
                f.read(),
                password=s.private_key_passphrase.encode() if s.private_key_passphrase else None,
            )
        args["private_key"] = key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    elif s.auth == "externalbrowser":
        args["authenticator"] = "externalbrowser"
    elif s.auth == "pat":
        if not s.token:
            raise SnowflakeError("SNOWFLAKE_CONFIGURATION")
        args["password"] = s.token
    return {k: v for k, v in args.items() if v is not None}


@contextmanager
def connection(settings: Settings | None = None) -> Iterator[snowflake.connector.SnowflakeConnection]:
    conn = None
    try:
        conn = snowflake.connector.connect(**_connect_args(settings or get_settings()))
        yield conn
    except SnowflakeError:
        raise
    except Exception:
        raise SnowflakeError() from None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def query(sql: str, params: Any = None, *, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Trusted application SQL only; values must use connector bind parameters."""
    selected = settings or get_settings()
    with connection(selected) as conn, conn.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute(sql, params, timeout=selected.statement_timeout_seconds)
        if cur.description is None:
            return []
        rows = cur.fetchmany(selected.max_query_rows + 1)
        if len(rows) > selected.max_query_rows:
            raise SnowflakeError("SNOWFLAKE_ROW_LIMIT")
        return rows
