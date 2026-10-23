"""Verify ingested NorthStar rows, runtime reads and optional live Cortex calls."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'backend'))
from app.domain import fingerprint, utc_now
from app.northstar_agent import AGENT_NAMES, TOOLS, agent_spec
from app.northstar_cloud import LiveNorthStarService
from app.platform import decoded, identifier


def verify_agents(source):
    result = {}
    with source.session():
        for kind, name in AGENT_NAMES.items():
            full_name = source.prefix + '.' + name
            rows = source.sql('DESCRIBE AGENT ' + full_name)
            if len(rows) != 1 or decoded(rows[0]['agent_spec']) != agent_spec(source.settings.cortex_model, kind):
                raise ValueError('Cloud Agent specification differs from the application: ' + name)
            spec_query_id = source.query_ids[-1]
            grants = source.sql('SHOW GRANTS ON AGENT ' + full_name)
            privileges = sorted(r['privilege'] for r in grants if r['grantee_name'] == source.settings.app_role)
            if not {'USAGE', 'MONITOR'} <= set(privileges):
                raise ValueError('Application-role Agent grants are incomplete: ' + name)
            result[kind] = dict(agent=full_name, spec_matches=True, app_role=source.settings.app_role,
                                privileges=privileges, spec_query_id=spec_query_id, grants_query_id=source.query_ids[-1])
    return result


def verify_execution(source, execution, snapshot_id):
    """Require actual delegated tool traces and matching persisted audit events."""
    executions = [execution, *execution.get('children', [])]
    if len(executions) != 3 or {e['kind'] for e in executions} != set(AGENT_NAMES):
        raise ValueError('Expected a coordinator and both native specialist executions')
    threads = {e['thread_id'] for e in executions}
    if len(threads) != 3:
        raise ValueError('Each native Agent must use its own thread')
    for item in executions:
        kind = item['kind']
        if item['agent'] != source.prefix + '.' + AGENT_NAMES[kind] or item.get('status') != 'COMPLETED':
            raise ValueError('Unexpected or incomplete native Agent execution')
        if item['snapshot_id'] != snapshot_id or len(item['tools']) != len(TOOLS[kind]) or {t['name'] for t in item['tools']} != set(TOOLS[kind]):
            raise ValueError('Native Agent tool coverage or snapshot binding failed')
        if any(t['snapshot_id'] != snapshot_id for t in item['tools']):
            raise ValueError('Native Agent tool read another snapshot')
        if not item['turns'] or any(t['thread_id'] != item['thread_id'] for t in item['turns']):
            raise ValueError('Native Agent response thread does not match its execution')
        with source.session():
            rows = source.sql(f'''SELECT PAYLOAD FROM {source.prefix}.AUDIT_EVENTS
                WHERE SNAPSHOT_ID=%s AND PAYLOAD:kind::VARCHAR=%s
                AND PAYLOAD:execution:thread_id::VARCHAR=%s''',
                (snapshot_id, 'agent_review_completed' if kind == 'coordinator' else 'delegated_agent_completed', item['thread_id']))
        if not any(decoded(r['PAYLOAD'])['execution'] == item for r in rows):
            raise ValueError('Native Agent execution is missing its matching cloud audit record')
    return {e['kind']: e for e in executions}


def verify(service, *, investigate=False, chat=False):
    catalog = json.loads((REPO / 'data/northstar_happy_path/generated/catalog.json').read_text())
    source = service.source
    union = ' UNION ALL '.join(f"SELECT '{name}' AS TABLE_NAME, OBJECT_CONSTRUCT_KEEP_NULL(*) AS PAYLOAD FROM {source.prefix}.SOURCE_{identifier(name)}" for name in catalog['tables'])
    with source.session():
        rows = source.sql(union)
    def normalize(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return int(value)
        return value
    actual = {name: [] for name in catalog['tables']}
    for row in rows:
        actual[row['TABLE_NAME']].append({k.lower(): normalize(v) for k,v in decoded(row['PAYLOAD']).items()})
    for table, expected in catalog['tables'].items():
        if sorted(map(fingerprint, actual[table])) != sorted(map(fingerprint, expected)):
            raise ValueError('Cloud rows do not match the source pack: ' + table)
    current = service.read()
    provisioning = verify_agents(source) if service.native_agents else None
    initial_state = {k: current[k] for k in ('revision', 'snapshot_id', 'decision', 'drafts')}
    cached = set(current['findings']) == {'operations', 'sourcing', 'qualification'}
    if investigate:
        current = service.investigate(current['revision'], current['snapshot_id'])
    if current['mode'] != 'live' or current['analysis']['recommended_plan_id'] != 'C':
        raise ValueError('Unexpected runtime provider or recommendation')
    plans = {p['id']: p for p in current['analysis']['plans']}
    if plans['C']['expenditure_cents'] != 2400000 or plans['C']['contracted_on_time'] != 120 or plans['C']['forecast_unbuilt'] != 130:
        raise ValueError('Live deterministic results do not match the independently validated NorthStar case')
    report = dict(status='passed', verified_at=utc_now(), provider='snowflake', ai_provider=current['cloud']['ai_provider'], schema=source.prefix, source_tables=len(actual), source_rows=sum(map(len, actual.values())), snapshot_id=current['snapshot_id'], prompt_version=service.prompt_version, investigation_cached=cached, current_case_approved=bool(current['decision']))
    if set(current['findings']) != {'operations', 'sourcing', 'qualification'}:
        raise ValueError('Three current-version live Claude findings are required')
    report['findings'] = {role: {k: finding[k] for k in ('text', 'citations', 'provider')}
                          for role, finding in current['findings'].items()}
    if service.native_agents:
        report['provisioning'] = provisioning
        execution = current['findings']['operations']['execution']
        if any(f['provider'] != 'snowflake_cortex_agent' or f['execution'] != execution for f in current['findings'].values()):
            raise ValueError('All three findings must belong to the same native investigation')
        report['investigation'] = verify_execution(source, execution, current['snapshot_id'])
    else:
        report['specialist_queries'] = {role: f['query_id'] for role, f in current['findings'].items()}
    if chat:
        answer = service.chat('What is the recovery approval budget, and are Apex and the hybrid within it? Keep the original Pacific PO separate.', current['revision'], current['snapshot_id'])
        report['company_brain'] = answer
        if service.native_agents:
            report['chat_execution'] = verify_execution(source, answer['execution'], current['snapshot_id'])
    after = service.read()
    if initial_state != {k: after[k] for k in initial_state}:
        raise ValueError('Case decisions, drafts or evidence changed during verification')
    report['case_state_unchanged'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--investigate', action='store_true')
    parser.add_argument('--chat', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.live:
        print('Use --live for cloud row and runtime verification. --investigate and --chat explicitly enable hosted inference.')
        return
    result = verify(LiveNorthStarService(), investigate=args.investigate, chat=args.chat)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
