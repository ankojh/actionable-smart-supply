"""Render/review setup locally; --apply explicitly creates dedicated cloud objects."""

from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings
from app.platform import identifier
from app.snowflake_client import connection
from snowflake.connector.util_text import split_statements


def statements(text):
    return [statement for statement, _ in split_statements(StringIO(text)) if statement.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute reviewed setup (cloud mutation; default render only)",
    )
    parser.add_argument(
        "--include-search",
        action="store_true",
        help="Create hourly Search service; billable indexing",
    )
    parser.add_argument(
        "--grant-user",
        help="Explicitly grant dedicated application and owner roles to this Snowflake user (not evaluator)",
    )
    args = parser.parse_args()
    settings = get_settings()
    values = {
        "DATABASE": identifier(settings.database or ""),
        "SCHEMA": identifier(settings.app_schema),
        "WAREHOUSE": identifier(settings.warehouse or ""),
    }
    files = sorted((ROOT / "snowflake" / "setup").glob("*.sql"))
    if not args.include_search:
        files = [f for f in files if f.name != "03_search.sql"]
    rendered = []
    for file in files:
        sql = file.read_text()
        for name, value in values.items():
            sql = sql.replace("{{" + name + "}}", value)
        rendered.append((file.name, sql))
    if not args.apply:
        for name, sql in rendered:
            print("-- " + name + "\n" + sql)
        return
    with connection(settings) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT CURRENT_ROLE()")
        original_role = identifier(cursor.fetchone()[0])
        for name, sql in rendered:
            cursor.execute("USE ROLE " + original_role)
            if name == "03_search.sql" and args.grant_user:
                user = '"' + args.grant_user.replace('"', '""') + '"'
                cursor.execute("GRANT ROLE PROJECT_RECOVERY_OWNER_V1 TO USER " + user)
                cursor.execute("GRANT ROLE PROJECT_RECOVERY_APP_V1 TO USER " + user)
            for statement in statements(sql):
                cursor.execute(statement)
            print(name + ": applied")
        if args.grant_user:
            cursor.execute("USE ROLE " + original_role)
            user = '"' + args.grant_user.replace('"', '""') + '"'
            cursor.execute("GRANT ROLE PROJECT_RECOVERY_OWNER_V1 TO USER " + user)
            cursor.execute("GRANT ROLE PROJECT_RECOVERY_APP_V1 TO USER " + user)
        conn.commit()


if __name__ == "__main__":
    main()
