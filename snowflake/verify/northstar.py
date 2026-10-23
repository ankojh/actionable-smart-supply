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
from app.domain import fingerprint
from app.northstar_cloud import LiveNorthStarService, PROMPT_VERSION
from app.platform import decoded, identifier


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
    if investigate:
        current = service.investigate(current['revision'], current['snapshot_id'])
    if current['mode'] != 'live' or current['analysis']['recommended_plan_id'] != 'C':
        raise ValueError('Unexpected runtime provider or recommendation')
    plans = {p['id']: p for p in current['analysis']['plans']}
    if plans['C']['expenditure_cents'] != 2400000 or plans['C']['contracted_on_time'] != 120 or plans['C']['forecast_unbuilt'] != 130:
        raise ValueError('Live deterministic results do not match the independently validated NorthStar case')
    report = dict(status='passed', provider='snowflake', schema=source.prefix, source_tables=len(actual), source_rows=sum(map(len, actual.values())), snapshot_id=current['snapshot_id'], prompt_version=PROMPT_VERSION, specialist_queries={role: f['query_id'] for role, f in current['findings'].items()}, current_case_approved=bool(current['decision']))
    if len(report['specialist_queries']) != 3:
        raise ValueError('Three current-version live Claude findings are required')
    if chat:
        answer = service.chat('What is the recovery approval budget, and are Apex and the hybrid within it? Keep the original Pacific PO separate.', current['revision'], current['snapshot_id'])
        report['company_brain'] = answer
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
