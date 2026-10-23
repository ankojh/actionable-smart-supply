from contextlib import contextmanager
from typing import Any, Iterator

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from .config import Settings, get_settings


def _connect_args(s: Settings) -> dict[str, Any]:
    args: dict[str, Any] = {
        "account": s.account,
        "user": s.user,
        "role": s.role,
        "warehouse": s.warehouse,
        "database": s.database,
        "schema": s.schema_,
        "application": "actionable-smart-supply",
    }
    if s.auth == "password":
        args["password"] = s.password
    elif s.auth == "keypair":
        if not s.private_key_path:
            raise ValueError("SNOWFLAKE_PRIVATE_KEY_PATH is required for keypair auth")
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
        # Snowflake accepts a PAT in place of a password, which works on every connector version
        if not s.token:
            raise ValueError("SNOWFLAKE_TOKEN is required for pat auth")
        args["password"] = s.token
    return {k: v for k, v in args.items() if v is not None}


@contextmanager
def connection() -> Iterator[snowflake.connector.SnowflakeConnection]:
    conn = snowflake.connector.connect(**_connect_args(get_settings()))
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
