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
    parser.add_argument("--update", action="store_true", help="Explicitly update existing named Agent specifications without replacing their objects or grants")
    parser.add_argument("--northstar", action="store_true", help="Provision Risk Detection, Recovery Plan and their coordinator")
    args = parser.parse_args()
    s = get_settings()
    prefix = identifier(s.database or "") + "." + ("NORTHSTAR_RECOVERY_V2" if args.northstar else identifier(s.app_schema))
    if args.northstar:
        from app.northstar_agent import AGENT_NAMES as NORTHSTAR_AGENTS
        from app.northstar_agent import agent_spec as northstar_spec
        labels = {'risk_detection': 'Risk Detection', 'recovery_plan': 'Recovery Plan', 'coordinator': 'NorthStar coordinator'}
        definitions = [(NORTHSTAR_AGENTS[kind], labels[kind], northstar_spec(s.cortex_model, kind))
                       for kind in ('risk_detection', 'recovery_plan', 'coordinator')]
    else:
        definitions = [(name, role.title(), agent_spec(role, s.cortex_model)) for role, name in AGENT_NAMES.items()]
    statements = []
    for name, label, definition in definitions:
        spec = json.dumps(definition)
        profile = json.dumps({"display_name": "sumo · " + label})
        statements.append(f"CREATE AGENT IF NOT EXISTS {prefix}.{name} COMMENT='sumo scoped recovery investigator' PROFILE='{profile}' FROM SPECIFICATION $${spec}$$")
        if args.update:
            statements.append(f"ALTER AGENT {prefix}.{name} MODIFY LIVE VERSION SET SPECIFICATION = $${spec}$$")
        statements.append(f"GRANT USAGE, MONITOR ON AGENT {prefix}.{name} TO ROLE {identifier(s.app_role)}")
    if not args.apply:
        print(";\n".join(statements) + ";")
        return
    with connection(s) as conn, conn.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    print(json.dumps({"status": "provisioned", "agents": [prefix + "." + n for n, _, _ in definitions]}))


if __name__ == "__main__":
    main()
