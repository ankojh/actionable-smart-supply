"""Validate NorthStar locally; --apply ingests typed tables and published snapshots."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'backend'))
sys.path.insert(0, str(REPO))
from app.config import get_settings
from app.domain import fingerprint
from app.northstar import CASE_ID, STAGES, load_stage
from app.platform import identifier
from app.snowflake_client import connection

PACK = REPO / 'data/northstar_happy_path/generated'
SCHEMA = 'NORTHSTAR_RECOVERY_V2'


def prepare():
    from data.northstar_happy_path.validate import validate
    validate(PACK)
    catalog = json.loads((PACK / 'catalog.json').read_text())
    dictionary = json.loads((PACK / 'data_dictionary.json').read_text())
    snapshots = [load_stage(i) for i in range(len(STAGES))]
    return catalog, dictionary, snapshots


def publish(settings, catalog, dictionary, snapshots):
    prefix = identifier(settings.database or '') + '.' + SCHEMA
    role = identifier(settings.app_role)
    ingestion_id = fingerprint(catalog)
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {prefix} COMMENT='sumo NorthStar V2 synthetic recovery workspace'")
        cur.execute(f'GRANT USAGE ON SCHEMA {prefix} TO ROLE {role}')
        for name, spec in dictionary.items():
            table = prefix + '.SOURCE_' + identifier(name)
            definitions = ', '.join('"' + c['name'].upper() + '" ' + c['snowflake_type'] for c in spec['columns'])
            cur.execute(f'CREATE TABLE IF NOT EXISTS {table} ({definitions})')
            cur.execute(f'GRANT SELECT ON TABLE {table} TO ROLE {role}')
        for name, columns in {
            'PUBLISHED_SNAPSHOTS': 'SNAPSHOT_ID VARCHAR, STAGE NUMBER, CASE_ID VARCHAR, PAYLOAD VARIANT, INGESTION_ID VARCHAR, PUBLISHED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()',
            'CASE_STATE': 'CASE_ID VARCHAR, REVISION NUMBER, PAYLOAD VARIANT',
            'AI_FINDINGS': 'SNAPSHOT_ID VARCHAR, ROLE VARCHAR, PAYLOAD VARIANT, CREATED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()',
            'AUDIT_EVENTS': 'EVENT_ID VARCHAR, CASE_ID VARCHAR, SNAPSHOT_ID VARCHAR, PAYLOAD VARIANT, CREATED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()',
            'INGESTIONS': 'INGESTION_ID VARCHAR, PAYLOAD VARIANT, CREATED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()',
        }.items():
            cur.execute(f'CREATE TABLE IF NOT EXISTS {prefix}.{name} ({columns})')
            permissions = 'SELECT,INSERT,UPDATE' if name == 'CASE_STATE' else 'SELECT,INSERT' if name in {'AI_FINDINGS', 'AUDIT_EVENTS'} else 'SELECT'
            cur.execute(f'GRANT {permissions} ON TABLE {prefix}.{name} TO ROLE {role}')
        cur.execute('BEGIN')
        try:
            for name, spec in dictionary.items():
                table = prefix + '.SOURCE_' + identifier(name)
                columns = [c['name'].upper() for c in spec['columns']]
                select = ', '.join('VALUE:"' + c['name'] + '"::' + c['snowflake_type'] + ' AS "' + c['name'].upper() + '"' for c in spec['columns'])
                keys = [k.upper() for k in spec['primary_key']]
                on = ' AND '.join(f'T."{k}"=S."{k}"' for k in keys)
                updates = ','.join(f'T."{k}"=S."{k}"' for k in columns if k not in keys)
                quoted = ','.join(f'"{k}"' for k in columns)
                values = ','.join(f'S."{k}"' for k in columns)
                cur.execute(f'MERGE INTO {table} T USING (SELECT {select} FROM TABLE(FLATTEN(INPUT=>PARSE_JSON(%s)))) S ON {on} WHEN MATCHED THEN UPDATE SET {updates} WHEN NOT MATCHED THEN INSERT ({quoted}) VALUES ({values})', (json.dumps(catalog['tables'][name]),))
            for snapshot in snapshots:
                digest = fingerprint(snapshot)
                cur.execute(f'MERGE INTO {prefix}.PUBLISHED_SNAPSHOTS T USING (SELECT %s SNAPSHOT_ID,%s STAGE,%s CASE_ID,PARSE_JSON(%s) PAYLOAD,%s INGESTION_ID) S ON T.SNAPSHOT_ID=S.SNAPSHOT_ID WHEN NOT MATCHED THEN INSERT (SNAPSHOT_ID,STAGE,CASE_ID,PAYLOAD,INGESTION_ID) VALUES (S.SNAPSHOT_ID,S.STAGE,S.CASE_ID,S.PAYLOAD,S.INGESTION_ID)', (digest, snapshot['metadata']['stage'], CASE_ID, json.dumps(snapshot), ingestion_id))
            state = dict(stage=4, revision=0, decision=None, drafts=[], monitoring_released=False)
            cur.execute(f'MERGE INTO {prefix}.CASE_STATE T USING (SELECT %s CASE_ID,0 REVISION,PARSE_JSON(%s) PAYLOAD) S ON T.CASE_ID=S.CASE_ID WHEN NOT MATCHED THEN INSERT (CASE_ID,REVISION,PAYLOAD) VALUES(S.CASE_ID,S.REVISION,S.PAYLOAD)', (CASE_ID, json.dumps(state)))
            manifest = dict(ingestion_id=ingestion_id, source='NorthStar V2 synthetic pack', table_counts={k: len(v) for k,v in catalog['tables'].items()}, planning_snapshots=len(snapshots))
            cur.execute(f'MERGE INTO {prefix}.INGESTIONS T USING (SELECT %s INGESTION_ID,PARSE_JSON(%s) PAYLOAD) S ON T.INGESTION_ID=S.INGESTION_ID WHEN NOT MATCHED THEN INSERT(INGESTION_ID,PAYLOAD) VALUES(S.INGESTION_ID,S.PAYLOAD)', (ingestion_id, json.dumps(manifest)))
            cur.execute('COMMIT')
        except Exception:
            cur.execute('ROLLBACK')
            raise
    return dict(status='published', schema=prefix, **manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    catalog, dictionary, snapshots = prepare()
    if args.apply:
        print(json.dumps(publish(get_settings(), catalog, dictionary, snapshots), indent=2))
    else:
        print(json.dumps(dict(status='validated', schema=SCHEMA, tables=len(dictionary), rows=sum(map(len,catalog['tables'].values())), snapshots=len(snapshots)), indent=2))


if __name__ == '__main__':
    main()
