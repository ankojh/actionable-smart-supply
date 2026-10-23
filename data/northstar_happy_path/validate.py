"""Local integrity and full-BOM schedule checks. Never opens a database."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .generate import ROOT, SCENARIO, SCHEMA, FOREIGN_KEYS, STAGES, fields, digest

def add_business_days(day, count):
    step=1 if count>=0 else -1
    while count:
        day+=timedelta(days=step)
        if day.weekday()<5: count-=step
    return day

def simulate(tables, additional_lots=(), end='2026-11-06'):
    """Additional lots are selected HDR supply, never all offers or recovery stock.

    This fixture oracle deliberately ignores other inbound POs: the full non-HDR
    BOM is covered by opening stock alone. Pacific's revised ETA is after horizon.
    """
    policy=json.loads((ROOT/'context/policies.json').read_text())
    stock={r['part_number']:r['on_hand_qty']-r['allocated_qty'] for r in tables['inventory']}
    bom=defaultdict(dict)
    for r in tables['bom']: bom[r['model']][r['part_number']]=r['qty_per_unit']
    plans={r['plan_id']:r for r in tables['production_plan']}
    orders=sorted(tables['customer_orders'],key=lambda r:(not r['contracted'],r['promised_ship_date'] or '9999-12-31',r['customer_order_id']))
    remaining={r['customer_order_id']:r['units'] for r in orders}
    calendar={r['calendar_date']:r for r in tables['production_calendar']}
    day=date.fromisoformat(policy['horizon_start']); stop=date.fromisoformat(end)
    schedule=[]
    while day<=stop:
        for eta,qty in additional_lots:
            if eta==day.isoformat(): stock['HDR-200']+=qty
        cap=calendar[day.isoformat()]['robot_start_capacity']
        for order in orders:
            if day.isoformat()<plans[order['plan_id']]['planned_start_date']: continue
            required=bom[order['model']]
            qty=min([remaining[order['customer_order_id']],cap]+[stock[p]//q for p,q in required.items()])
            if qty:
                for p,q in required.items(): stock[p]-=q*qty
                remaining[order['customer_order_id']]-=qty; cap-=qty
                schedule.append({'customer_order_id':order['customer_order_id'],'start_date':day.isoformat(),'ship_date':add_business_days(day,policy['material_to_ship_business_days']).isoformat(),'units':qty})
        day+=timedelta(days=1)
    risk={}
    for order in orders:
        if order['contracted']:
            on_time=sum(r['units'] for r in schedule if r['customer_order_id']==order['customer_order_id'] and r['ship_date']<=order['promised_ship_date'])
            if order['units']>on_time: risk[order['customer_order_id']]=order['units']-on_time
    prices={r['customer_order_id']:r['unit_revenue_cents'] for r in orders}
    return {'schedule':schedule,'at_risk':risk,'exposure_cents':sum(q*prices[k] for k,q in risk.items()),'remaining_orders':remaining,'ending_inventory':stock,'robots_started':sum(r['units'] for r in schedule)}

def check_hybrid_feasibility(tables, as_of='2026-10-26'):
    """Evidence gate for the fixed 80 + 40 proposal; plan approval is separate."""
    offers={r['offer_id']:r for r in tables['supplier_offers']}
    offer=offers.get('OFFER-C')
    releases=tables['inventory_releases']
    if not offer or not releases: return False
    release=releases[0]
    recovery=next(r for r in tables['recovery_inventory'] if r['recovery_id']==release['recovery_id'])
    approved=any(q['supplier_id']==offer['supplier_id'] and q['part_number']==offer['part_number'] and q['status']=='approved' and q['valid_from']<=as_of<=q['valid_to'] for q in tables['supplier_qualifications'])
    return bool(approved and offer['offer_status']=='confirmed' and offer['confirmed_receipt_date'] and offer['minimum_order_qty']<=40<=offer['offered_qty'] and as_of<=offer['acceptance_deadline'] and offer['confirmed_receipt_date']<='2026-11-04' and release['approved_qty']>=80 and release['usable_date']<='2026-11-04' and recovery['technically_eligible'] and recovery['condition_status']=='Usable' and recovery['recoverable_qty']>=80)

def validate(output):
    output=Path(output); catalog=json.loads((output/'catalog.json').read_text()); tables=catalog['tables']
    manifest=json.loads((output/'manifest.json').read_text())
    checks=0
    def check(ok,message):
        nonlocal checks
        checks+=1
        if not ok: raise ValueError(message)
    for relative,sha in manifest['files'].items():
        check(digest((output/relative).read_bytes())==sha,'Checksum mismatch: '+relative)
    for name,sha in manifest['source_hashes'].items():
        check(digest((ROOT/'source_pack'/name).read_bytes())==sha,'Source changed: '+name)
    policy=json.loads((ROOT/'context/policies.json').read_text())
    check(digest((ROOT/'context/policies.json').read_bytes())==manifest['policy_sha256'],'Policy changed')
    dictionary=json.loads((output/'data_dictionary.json').read_text())
    for table,rows in tables.items():
        check(len(rows)==manifest['row_counts'][table],f'{table} row count')
        keys=SCHEMA[table][0].split()
        check(len({tuple(r[k] for k in keys) for r in rows})==len(rows),f'{table} duplicate key')
        check([c['name'] for c in dictionary[table]['columns']]==[n for n,_ in fields(table)],f'{table} dictionary')
        for row in rows:
            check(set(row)=={n for n,_ in fields(table)},f'{table} field mismatch')
            check(row['scenario_id']==SCENARIO,f'{table} scenario scope')
            check(0<=row['available_stage']<len(STAGES),f'{table} stage range')
            for field,kind in fields(table):
                value=row[field]; nullable=kind.endswith('?'); kind=kind.rstrip('?')
                if value is None:
                    check(nullable,f'{table}.{field} unexpected null'); continue
                check(type(value)=={'str':str,'int':int,'bool':bool,'date':str}[kind],f'{table}.{field} incorrect type')
                if kind=='date': date.fromisoformat(value)
                if field.endswith(('_qty','_cents')): check(value>=0,f'{table}.{field} negative value')
        nd=[json.loads(line) for line in (output/'tables'/(table+'.ndjson')).read_text().splitlines()]
        check(nd==rows,f'{table} NDJSON mismatch')
        with (output/'tables'/(table+'.csv')).open(newline='') as f: csv_rows=list(csv.DictReader(f))
        decoded=[]
        for row in csv_rows:
            new={}
            for field,kind in fields(table):
                value=row[field]; kind=kind.rstrip('?')
                new[field]=None if value=='\\N' else int(value) if kind=='int' else value=='true' if kind=='bool' else value
            decoded.append(new)
        check(decoded==rows,f'{table} CSV mismatch')
    for i,(name,day,_) in enumerate(STAGES):
        stage_snapshot=json.loads((output/'stages'/name/'dataset.json').read_text())
        check(stage_snapshot['policies']==policy,f'{name} policy drift')
        snap=stage_snapshot['tables']
        check(snap=={t:[r for r in rows if r['available_stage']<=i] for t,rows in tables.items()},f'{name} stage leakage')
        for table,column,target,key in FOREIGN_KEYS:
            allowed={r[key] for r in snap[target]}
            check(all(r[column] is None or r[column] in allowed for r in snap[table]),f'{name} broken link {table}.{column}')
        for doc in snap['documents']:
            check(digest(doc['content'].encode())==doc['sha256'],f"Document hash {doc['document_id']}")
            check((output/'stages'/name/'documents'/doc['file_name']).read_text()==doc['content'],f"Document export {doc['document_id']}")
            check(doc['document_date']<=day,f'{name} future evidence')
        for link in snap['document_links']:
            key_fields=SCHEMA[link['entity_table']][0].split()
            check(len(key_fields)==1 and any(r[key_fields[0]]==link['entity_key'] for r in snap[link['entity_table']]),'Evidence bridge missing target')
        for r in snap['inventory']: check(r['snapshot_date']<=day,'Future inventory leak')
    # Independent, hand-specified expected results are never generated or exposed to stages.
    expected=json.loads((ROOT/'tests/expected_results.json').read_text())
    check(sum(p['planned_build_qty'] for p in tables['production_plan'])==250,'Total production demand')
    for plan in tables['production_plan']:
        check(sum(o['units'] for o in tables['customer_orders'] if o['plan_id']==plan['plan_id'])==plan['planned_build_qty'],'Customer demand does not reconcile to plan')
    for order in tables['customer_orders']:
        expected_need=add_business_days(date.fromisoformat(order['promised_ship_date']),-2).isoformat() if order['promised_ship_date'] else None
        check(order['material_need_by_date']==expected_need,'Incorrect material need-by')
    inventory={r['part_number']:r for r in tables['inventory']}
    for component in tables['bom']:
        if component['part_number']=='HDR-200': continue
        inv=inventory[component['part_number']]
        check(inv['on_hand_qty']-inv['allocated_qty']-250*component['qty_per_unit']>=inv['safety_stock_qty'],'Non-HDR component shortage')
    offers={r['offer_id']:r for r in tables['supplier_offers']}; rel=tables['inventory_releases'][0]
    options={'baseline':[], 'A':[(offers['OFFER-APEX-EMERGENCY']['confirmed_receipt_date'],400)],'B':[(rel['usable_date'],80)],'C':[(rel['usable_date'],80),(offers['OFFER-C']['confirmed_receipt_date'],40)]}
    results={}
    for option,lots in options.items():
        result=simulate(tables,lots); results[option]=result
        for key in ('at_risk','exposure_cents','robots_started'):
            check(result[key]==expected[option][key],f'{option} {key}')
        check(result['ending_inventory']['HDR-200']==expected[option]['ending_hdr_qty'],f'{option} HDR balance')
        check(result['remaining_orders']['FC-001']==expected[option]['forecast_unbuilt'],f'{option} forecast')
        for order,ship in expected[option].get('ship_dates',{}).items():
            check(max(r['ship_date'] for r in result['schedule'] if r['customer_order_id']==order)==ship,f'{option} ship date {order}')
        daily=defaultdict(int)
        for row in result['schedule']: daily[row['start_date']]+=row['units']
        check(max(daily.values())<=60,f'{option} shared capacity')
        check(all(v>=0 for v in result['ending_inventory'].values()),f'{option} negative BOM stock')
    check(400*offers['OFFER-APEX-EMERGENCY']['unit_price_cents']==19440000,'A cash')
    check(40*offers['OFFER-C']['unit_price_cents']+rel['handling_cost_cents']==2400000,'C cash')
    check(19440000-2400000==17040000,'Upfront reduction')
    check(400*(48600-41200)==2960000,'A purchase premium')
    check(check_hybrid_feasibility(tables),'Hybrid evidence gate')
    pos={r['po_number']:r for r in tables['purchase_orders']}
    check(pos['PO-2026-1042']['latest_eta']=='2026-10-20' and pos['PO-2026-1042']['status']=='Open','ERP overwritten')
    check(pos['PO-2026-0961']['open_qty']==0,'Received PO became inbound')
    check([p['po_number'] for p in pos.values() if p['part_number']=='HDR-200' and p['open_qty']>0]==['PO-2026-1042'],'Extra primary inbound')
    check(all(r['incremental_cost_cents'] is None for r in tables['recovery_inventory']),'Unknown cost changed to zero')
    check(all(r['status']=='draft' and not r['transmitted'] for r in tables['action_drafts']),'Action automatically sent')
    report={'status':'passed','checks':checks,'scenario_id':SCENARIO,'options':{k:{x:v[x] for x in ('at_risk','exposure_cents','robots_started','remaining_orders')} for k,v in results.items()}}
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'generated')
    args=parser.parse_args()
    print(json.dumps(validate(args.output),indent=2))
