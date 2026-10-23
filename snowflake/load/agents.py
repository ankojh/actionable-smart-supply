"""Render or explicitly provision only this application's managed Cortex Agents."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings
from app.cortex import AGENT_NAMES, agent_spec
from app.platform import identifier
from app.snowflake_client import connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    s = get_settings()
    prefix = identifier(s.database or "") + "." + identifier(s.app_schema)
    statements = []
    for role, name in AGENT_NAMES.items():
        spec = json.dumps(agent_spec(role, s.cortex_model))
        profile = json.dumps({"display_name": "sumo · " + role.title()})
        statements.extend([
            f"CREATE AGENT IF NOT EXISTS {prefix}.{name} COMMENT='sumo scoped recovery investigator' PROFILE='{profile}' FROM SPECIFICATION $${spec}$$",
            f"GRANT USAGE, MONITOR ON AGENT {prefix}.{name} TO ROLE {identifier(s.app_role)}",
        ])
    if not args.apply:
        print(";\n".join(statements) + ";")
        return
    with connection(s) as conn, conn.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    print(json.dumps({"status": "provisioned", "agents": [prefix + "." + n for n in AGENT_NAMES.values()]}))


if __name__ == "__main__":
    main()
