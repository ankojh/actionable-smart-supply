"""Generate deterministic, local-only NorthStar data. Python standard library only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'source_pack'
CASE = 'NS-2026-1042'
SCENARIO = 'northstar_happy_path_v2'

# A ? suffix means nullable. Monetary values are integer USD cents everywhere.
# All records carry scenario_id, source_ref and available_stage.
SCHEMA = {
    'products': ('model', 'model:str product_name:str', 'One finished product.'),
    'parts': ('part_number', 'part_number:str part_name:str unit_of_measure:str', 'One component, counted in each.'),
    'suppliers': ('supplier_id', 'supplier_id:str supplier_name:str', 'Supplier master; approval is part-specific.'),
    'customers': ('customer_id', 'customer_id:str customer_name:str is_forecast:bool', 'Customers and the internal forecast bucket.'),
    'locations': ('location_id', 'location_id:str location_name:str location_type:str', 'Physically distinct stock pools.'),
    'bom': ('model part_number', 'model:str part_number:str qty_per_unit:int', 'Components consumed per robot; never add demand twice.'),
    'inventory': ('part_number location_id snapshot_date', 'part_number:str location_id:str on_hand_qty:int allocated_qty:int safety_stock_qty:int snapshot_date:date', 'Warehouse stock only. Pilot and R&D stock live exclusively in recovery_inventory.'),
    'purchase_orders': ('po_number', 'po_number:str part_number:str supplier_id:str qty:int unit_price_cents:int original_eta:date latest_eta:date status:str open_qty:int', 'ERP truth retained; only open_qty can represent future supply.'),
    'production_plan': ('plan_id', 'plan_id:str model:str planned_build_qty:int planned_start_date:date planned_completion_date:date priority:str', 'Demand by plan. Shared capacity comes from production_calendar, not each plan.'),
    'customer_orders': ('customer_order_id', 'customer_order_id:str customer_id:str model:str units:int promised_ship_date:date? material_need_by_date:date? contracted:bool unit_revenue_cents:int plan_id:str', 'Contracted before forecast; earliest promise then ascending order ID. Need-by is derived from promise minus two working days.'),
    'recovery_inventory': ('recovery_id', 'recovery_id:str part_number:str location_id:str recoverable_qty:int usable_date:date condition_status:str technically_eligible:bool requires_release_approval:bool incremental_cost_cents:int?', 'Physically separate reserved stock; eligibility is not release authorization. Unknown cost stays null.'),
    'production_calendar': ('calendar_date', 'calendar_date:date is_working_day:bool robot_start_capacity:int material_to_ship_business_days:int', 'One shared Fremont line; Mon-Fri, no holidays in this fictional calendar.'),
    'documents': ('document_id', 'document_id:str document_type:str document_date:date file_name:str content:str sha256:str', 'Evidence, not executable instructions; stage controls when a document may be retrieved.'),
    'document_links': ('document_id entity_table entity_key', 'document_id:str entity_table:str entity_key:str', 'Relational evidence bridge, one entity per row.'),
    'supplier_qualifications': ('qualification_id', 'qualification_id:str supplier_id:str part_number:str specification:str status:str valid_from:date valid_to:date document_id:str', 'Qualification alone does not guarantee quantity or arrival.'),
    'supplier_offers': ('offer_id', 'offer_id:str supplier_id:str part_number:str offered_qty:int minimum_order_qty:int unit_price_cents:int fixed_delivery_cost_cents:int? confirmed_receipt_date:date? acceptance_deadline:date standard_lead_business_days:int? offer_status:str document_id:str', 'Optional supply; never inventory until a selected, authorized plan uses it.'),
    'inventory_releases': ('release_id', 'release_id:str recovery_id:str approved_qty:int approval_date:date release_date:date usable_date:date handling_cost_cents:int cost_basis:str approver_actor_id:str document_id:str', 'Synthetic operational release authorization; execution is a separate later event.'),
    'case_events': ('event_id', 'event_id:str case_id:str event_type:str event_date:date scenario_version:int actor_id:str message:str', 'Scripted fixture events for one case; no event performs a real action.'),
    'actors': ('actor_id', 'actor_id:str display_name:str organization:str role:str email:str?', 'Fictional role-play identities. All email domains are reserved .example domains.'),
    'messages': ('message_id', 'message_id:str thread_id:str in_reply_to_message_id:str? case_id:str scenario_version:int sender_actor_id:str channel:str subject:str content:str document_id:str?', 'Staged role-play input. Supplier claims need their linked evidence.'),
    'message_recipients': ('message_id actor_id recipient_type', 'message_id:str actor_id:str recipient_type:str', 'One recipient per row; no comma-separated lists.'),
    'scenario_requests': ('request_id', 'request_id:str case_id:str scenario_version:int parent_version:int transfer_qty:int purchase_qty:int objective:str actor_id:str', 'Manager proposal, not a validated or approved plan.'),
    'plan_approvals': ('approval_id', 'approval_id:str case_id:str scenario_version:int option_id:str actor_id:str approved_date:date authorized_purchase_cents:int authorized_transfer_cents:int authority_limit_cents:int authorization_scope:str', 'Role-play authorization to prepare drafts only; never supplier submission.'),
    'action_drafts': ('draft_id', 'draft_id:str case_id:str scenario_version:int approval_id:str action_type:str part_number:str qty:int supplier_id:str? source_location_id:str? destination_location_id:str unit_price_cents:int? total_cents:int required_usable_date:date status:str transmitted:bool', 'Expected draft examples released only after scripted approval.'),
    'monitoring_events': ('monitor_id', 'monitor_id:str case_id:str scenario_version:int event_date:date event_type:str entity_id:str qty:int? status:str', 'Future simulation events; never evidence for initial recommendation.'),
}

STAGES = [
    ('00_detection', '2026-10-23', 'Potential disruption; impact not yet calculated.'),
    ('01_impact', '2026-10-26', 'Warehouse and demand snapshot released; compute baseline impact.'),
    ('02_initial_options', '2026-10-26', 'Apex confirmation available; A feasible, B conditional.'),
    ('03_manager_challenge', '2026-10-26', 'Manager requests V2; Supplier C and release still unconfirmed.'),
    ('04_hybrid_validated', '2026-10-26', 'New supplier and pilot release evidence available; rerun schedule.'),
    ('05_manager_approval', '2026-10-26', 'Scripted approval to prepare the V2 drafts, not to send them.'),
    ('06_drafts_ready', '2026-10-26', 'Transfer and purchase draft examples; nothing transmitted.'),
    ('07_monitoring', '2026-10-29', 'Simulated execution feed, only after an explicit facilitator release.'),
]

FOREIGN_KEYS = [
    ('bom','model','products','model'), ('bom','part_number','parts','part_number'),
    ('inventory','part_number','parts','part_number'), ('inventory','location_id','locations','location_id'),
    ('purchase_orders','supplier_id','suppliers','supplier_id'), ('purchase_orders','part_number','parts','part_number'),
    ('production_plan','model','products','model'), ('customer_orders','model','products','model'),
    ('customer_orders','customer_id','customers','customer_id'), ('customer_orders','plan_id','production_plan','plan_id'),
    ('recovery_inventory','part_number','parts','part_number'), ('recovery_inventory','location_id','locations','location_id'),
    ('document_links','document_id','documents','document_id'),
    ('supplier_qualifications','supplier_id','suppliers','supplier_id'), ('supplier_qualifications','part_number','parts','part_number'),
    ('supplier_qualifications','document_id','documents','document_id'),
    ('supplier_offers','supplier_id','suppliers','supplier_id'), ('supplier_offers','part_number','parts','part_number'),
    ('supplier_offers','document_id','documents','document_id'),
    ('inventory_releases','recovery_id','recovery_inventory','recovery_id'), ('inventory_releases','document_id','documents','document_id'),
    ('inventory_releases','approver_actor_id','actors','actor_id'), ('case_events','actor_id','actors','actor_id'),
    ('messages','sender_actor_id','actors','actor_id'), ('messages','document_id','documents','document_id'),
    ('messages','in_reply_to_message_id','messages','message_id'), ('message_recipients','message_id','messages','message_id'),
    ('message_recipients','actor_id','actors','actor_id'), ('scenario_requests','actor_id','actors','actor_id'),
    ('plan_approvals','actor_id','actors','actor_id'), ('action_drafts','approval_id','plan_approvals','approval_id'),
    ('action_drafts','part_number','parts','part_number'), ('action_drafts','supplier_id','suppliers','supplier_id'),
    ('action_drafts','source_location_id','locations','location_id'), ('action_drafts','destination_location_id','locations','location_id'),
]

def fields(table):
    result = [('scenario_id','str'),('source_ref','str'),('available_stage','int')]
    return result + [tuple(x.split(':')) for x in SCHEMA[table][1].split()]

def read_csv(name):
    with (SOURCE / (name+'.csv')).open(newline='', encoding='utf-8') as f:
        return [{k: None if v in ('NULL','') else v for k,v in r.items()} for r in csv.DictReader(f)]

def cents(value):
    return None if value is None else int(Decimal(value)*100)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def build():
    tables = {name: [] for name in SCHEMA}
    def add(table, stage=0, source='authored fixture extension', **row):
        tables[table].append(dict(scenario_id=SCENARIO,source_ref=source,available_stage=stage,**row))
    add('products',source='SPEC.md',model='NS-AMR-200',product_name='Warehouse autonomous mobile robot')
    for loc,name,kind in [('FREMONT-WH','Fremont Warehouse','warehouse'),('PILOT','Pilot Line','reserved'),('RD','R&D Test Bench','inspection'),('PRODUCTION','Fremont Production','production')]:
        add('locations',location_id=loc,location_name=name,location_type=kind)
    for r in read_csv('bom'):
        add('parts',source='bom.csv',part_number=r['part_number'],part_name=r['part_name'],unit_of_measure='each')
        add('bom',source='bom.csv',model=r['model'],part_number=r['part_number'],qty_per_unit=int(r['qty_per_unit']))
    supplier_names = sorted({r['supplier'] for r in read_csv('purchase_orders')} | {'Apex Drive Technologies','Summit Precision Drives'})
    suppliers = {n: 'SUP-'+str(i+1).zfill(3) for i,n in enumerate(supplier_names)}
    for n,sid in suppliers.items():
        add('suppliers',source='purchase_orders.csv / fictional Supplier C extension',supplier_id=sid,supplier_name=n)
    for r in read_csv('inventory'):
        add('inventory',1,'inventory.csv',part_number=r['part_number'],location_id='FREMONT-WH',on_hand_qty=int(r['on_hand_qty']),allocated_qty=int(r['allocated_qty']),safety_stock_qty=int(r['safety_stock_qty']),snapshot_date=r['snapshot_date'])
    for r in read_csv('purchase_orders'):
        add('purchase_orders',source='purchase_orders.csv',po_number=r['po_number'],part_number=r['part_number'],supplier_id=suppliers[r['supplier']],qty=int(r['qty']),unit_price_cents=cents(r['unit_price_usd']),original_eta=r['original_eta'],latest_eta=r['latest_eta'],status=r['status'],open_qty=int(r['open_qty']))
    for r in read_csv('production_plan'):
        add('production_plan',1,'production_plan.csv',**{k:r[k] for k in ['plan_id','model','planned_start_date','planned_completion_date','priority']},planned_build_qty=int(r['planned_build_qty']))
    for i,r in enumerate(read_csv('customer_orders'),1):
        cid='CUS-'+str(i).zfill(3)
        add('customers',source='customer_orders.csv',customer_id=cid,customer_name=r['customer'],is_forecast=r['contracted']=='N')
        need_by=None
        if r['promised_ship_date']:
            day=date.fromisoformat(r['promised_ship_date']); remaining=2
            while remaining:
                day-=timedelta(days=1)
                if day.weekday()<5: remaining-=1
            need_by=day.isoformat()
        add('customer_orders',1,'customer_orders.csv + company_policies.yaml',customer_order_id=r['customer_order_id'],customer_id=cid,model=r['model'],units=int(r['units']),promised_ship_date=r['promised_ship_date'],material_need_by_date=need_by,contracted=r['contracted']=='Y',unit_revenue_cents=cents(r['unit_revenue_usd']),plan_id=r['plan_id'])
    for r in read_csv('recovery_inventory'):
        add('recovery_inventory',1,'recovery_inventory.csv + updated Notion happy case',recovery_id=r['recovery_id'],part_number=r['part_number'],location_id='PILOT' if r['recovery_id']=='REC-001' else 'RD',recoverable_qty=int(r['recoverable_qty']),usable_date=r['available_date'],condition_status=r['condition_status'],technically_eligible=r['approved_for_use']=='Y',requires_release_approval=True,incremental_cost_cents=None)
    d=date(2026,10,23)
    while d<=date(2026,11,20):
        working=d.weekday()<5
        add('production_calendar',source='company_policies.yaml',calendar_date=d.isoformat(),is_working_day=working,robot_start_capacity=60 if working else 0,material_to_ship_business_days=2)
        d+=timedelta(days=1)
    actors=[
        ('MANAGER','Maya Chen','NorthStar Robotics','Supply Chain Manager','maya.chen@northstar.example'),
        ('BUYER','Alex Morgan','NorthStar Robotics','Buyer','alex.morgan@northstar.example'),
        ('PLANNER','Riley Park','NorthStar Robotics','Production Planner','riley.park@northstar.example'),
        ('PILOT-OWNER','Elena Brooks','NorthStar Robotics','Pilot Line Owner','elena.brooks@northstar.example'),
        ('QUALITY','Jordan Lee','NorthStar Robotics','Supplier Quality','jordan.lee@northstar.example'),
        ('FINANCE','Sam Taylor','NorthStar Robotics','Finance Partner','sam.taylor@northstar.example'),
        ('PACIFIC','Daniel Reyes','Pacific Motion Systems','Account Manager','daniel.reyes@pacific-motion.example'),
        ('APEX','Priya Natarajan','Apex Drive Technologies','Key Account Manager','priya.natarajan@apex-drive.example'),
        ('SUMMIT','Owen Patel','Summit Precision Drives','Sales Manager','owen.patel@summit-drives.example'),
        ('BRAIN','Company Brain','NorthStar Robotics','Orchestrator',None),
    ]
    for actor,name,org,role,email in actors:
        add('actors',actor_id=actor,display_name=name,organization=org,role=role,email=email)
    def doc(did,kind,day,content,stage,source,links=()):
        add('documents',stage,source,document_id=did,document_type=kind,document_date=day,file_name=did+'.md',content=content,sha256=digest(content.encode()))
        for table,key in links:
            add('document_links',stage,source,document_id=did,entity_table=table,entity_key=key)
    doc('DOC-SUP-001','supplier_email','2026-10-16',(SOURCE/'DOC-SUP-001_updated_supplier_email.md').read_text(),0,'DOC-SUP-001_updated_supplier_email.md',[('purchase_orders','PO-2026-1042'),('parts','HDR-200')])
    doc('DOC-QUOTE-001','supplier_quote','2026-10-22',(SOURCE/'backup_supplier_quote.md').read_text(),0,'backup_supplier_quote.md',[('parts','HDR-200')])
    doc('DOC-APEX-002','supplier_confirmation','2026-10-26',(SOURCE/'apex_emergency_confirmation.md').read_text(),2,'apex_emergency_confirmation.md',[('parts','HDR-200')])
    extensions=[
        ('DOC-PLAN-001','planning_note',1,'PLANNER','Production calendar and allocation',
         'The October 26 opening snapshot has 120 usable warehouse HDR-200 drives. The 80 pilot-line drives are a separate pool and remain reserved until the pilot owner approves release. There are no finished robots or WIP in this fixture. One Fremont line starts at most 60 robots per business day across both plans. Material usable on day zero can start that day; shipment follows two business days later. Protect contracted orders first, sorted by promise date and then sales order ID. SwiftLog material need-by is November 3; BayShip and NovaFab material need-by is November 4. Forecast FC-001 may be deferred.', [('production_plan','PLAN-001'),('production_plan','PLAN-002')]),
        ('DOC-PILOT-001','inventory_check',1,'PILOT-OWNER','Pilot stock is reserved, not released',
         'REC-001 contains 80 usable HDR-200 Rev C drives at the Pilot Line, outside the warehouse snapshot. They are technically eligible for production but reserved for pilot work. Transfer requires my release authorization and a manager-approved transfer request. Earliest physical availability is October 28. Handling cost has not yet been established. REC-002 contains 16 R&D drives requiring inspection and is not eligible.', [('recovery_inventory','REC-001'),('recovery_inventory','REC-002')]),
        ('DOC-QUAL-APEX','qualification_record',2,'QUALITY','Approved HDR-200 suppliers',
         'Pacific Motion Systems and Apex Drive Technologies are approved for HDR-200 Rev C, ratio 100:1. Apex AVL reference AVL-HDR-200-02 is valid January 1 through December 31, 2026. This qualification does not itself confirm stock or delivery dates.', [('suppliers',suppliers['Apex Drive Technologies'])]),
        ('DOC-QUAL-C','qualification_record',4,'QUALITY','Supplier C qualification',
         'Summit Precision Drives is the fictional Supplier C for this demo. AVL-HDR-200-03 approves Summit for HDR-200 Rev C, ratio 100:1, January 1 through December 31, 2026. Review completed October 26. The reserved 40-drive lot meets the same NorthStar specification; certificates accompany delivery and inspection is completed before shipment.', [('suppliers',suppliers['Summit Precision Drives'])]),
        ('DOC-QUOTE-C','supplier_quote',4,'SUMMIT','Quote SPD-2026-0040',
         'Summit Precision Drives offers 40 HDR-200 Rev C drives at USD 550.00 each; MOQ 40, total USD 22,000.00. Delivery to Fremont and incoming-inspection release are included, with no additional freight charge. Net 30 from delivery. Quote valid through October 27, 2026. This quote alone is not a delivery commitment; see the separate allocation confirmation.', [('parts','HDR-200')]),
        ('DOC-CONF-C','supplier_confirmation',4,'SUMMIT','Reserved 40-drive lot for NorthStar',
         'For quote SPD-2026-0040, we confirm 40 HDR-200 Rev C drives at USD 550.00 each, USD 22,000.00 total, with MOQ 40. The full lot will be received and usable at Fremont on October 29, 2026, subject to PO acceptance by October 27, 2026, end of day America/Los_Angeles. Freight and inspection are included; fixed additional delivery cost is USD 0.00. Allocation expires after that deadline. No order has yet been placed.', [('parts','HDR-200')]),
        ('DOC-PILOT-RELEASE','inventory_release',4,'PILOT-OWNER','Release authorization for REC-001',
         'On October 26, 2026 I authorize release of all 80 usable HDR-200 Rev C drives from REC-001 on October 27, conditional on the manager approving the transfer request. Transfer completes and material is usable in production on October 28. Pilot work can be deferred until after November 10 and has no contracted customer commitment in this fixture. This does not authorize an executed stock movement. The R&D lot REC-002 remains excluded.', [('recovery_inventory','REC-001')]),
        ('DOC-COST-001','cost_assumption',4,'FINANCE','Hybrid transfer and cash assumptions',
         'For NS-2026-1042 scenario V2, assume a one-time USD 2,000.00 handling and internal transfer cost for the 80-drive REC-001 movement. This is a fictional planning estimate, not an invoice. No other incremental fee is modeled. Supplier C purchase is USD 22,000.00; stated immediate expenditure is USD 24,000.00. Immediate expenditure is the demo comparison of commitments, not a literal cash-payment schedule under Net 30. Pacific PO-2026-1042 remains open at USD 164,800.00. The difference from Apex is upfront commitment reduction, not net economic savings or recovered damages.', [('recovery_inventory','REC-001'),('purchase_orders','PO-2026-1042')]),
    ]
    for did,kind,stage,actor,subject,body,links in extensions:
        person=next(a for a in actors if a[0]==actor)
        content=f'# {subject}\n\nSynthetic role-play evidence.\n\nFrom: {person[1]}, {person[2]}\nTo: Alex Morgan, NorthStar Robotics\nDate: 2026-10-26\nCase: {CASE}\n\n{body}\n'
        doc(did,kind,'2026-10-26',content,stage,'authored extension of updated Notion happy case',links)
    for qid,name,docid,stage in [('AVL-HDR-200-02','Apex Drive Technologies','DOC-QUAL-APEX',2),('AVL-HDR-200-03','Summit Precision Drives','DOC-QUAL-C',4)]:
        add('supplier_qualifications',stage,docid,qualification_id=qid,supplier_id=suppliers[name],part_number='HDR-200',specification='HDR-200 Rev C; ratio 100:1',status='approved',valid_from='2026-01-01',valid_to='2026-12-31',document_id=docid)
    for oid,name,qty,moq,price,receipt,deadline,lead,status,did,stage in [
        ('OFFER-APEX-STANDARD','Apex Drive Technologies',400,400,486,None,'2026-10-31',10,'quoted','DOC-QUOTE-001',0),
        ('OFFER-APEX-EMERGENCY','Apex Drive Technologies',400,400,486,'2026-10-29','2026-10-27',None,'confirmed','DOC-APEX-002',2),
        ('OFFER-C','Summit Precision Drives',40,40,550,'2026-10-29','2026-10-27',None,'confirmed','DOC-CONF-C',4)]:
        add('supplier_offers',stage,did,offer_id=oid,supplier_id=suppliers[name],part_number='HDR-200',offered_qty=qty,minimum_order_qty=moq,unit_price_cents=price*100,fixed_delivery_cost_cents=None if stage==0 else 0,confirmed_receipt_date=receipt,acceptance_deadline=deadline,standard_lead_business_days=lead,offer_status=status,document_id=did)
    add('inventory_releases',4,'DOC-PILOT-RELEASE + DOC-COST-001',release_id='REL-001',recovery_id='REC-001',approved_qty=80,approval_date='2026-10-26',release_date='2026-10-27',usable_date='2026-10-28',handling_cost_cents=200000,cost_basis='fictional_estimate',approver_actor_id='PILOT-OWNER',document_id='DOC-PILOT-RELEASE')
    def message(mid,stage,sender,subject,content,document=None,reply=None,thread='CASE-CHAT',recipient='BUYER',channel='email'):
        add('messages',stage,'authored role-play / linked source',message_id=mid,thread_id=thread,in_reply_to_message_id=reply,case_id=CASE,scenario_version=2 if stage>=3 else 1,sender_actor_id=sender,channel=channel,subject=subject,content=content,document_id=document)
        add('message_recipients',stage,message_id=mid,actor_id=recipient,recipient_type='to')
    for mid,did,actor,stage,thread in [('MSG-PACIFIC','DOC-SUP-001','PACIFIC',0,'PACIFIC-1042'),('MSG-APEX-QUOTE','DOC-QUOTE-001','APEX',0,'APEX-1187'),('MSG-APEX-CONF','DOC-APEX-002','APEX',2,'APEX-1187')]:
        document=next(d for d in tables['documents'] if d['document_id']==did)
        message(mid,stage,actor,did,document['content'],did,reply='MSG-APEX-REQUEST' if stage==2 else None,thread=thread)
    message('MSG-APEX-REQUEST',1,'BUYER','Emergency availability request','Please confirm a qualified HDR-200 lot, MOQ, all-in price, usable receipt date and acceptance deadline. The standard quote alone cannot establish timely supply.',reply='MSG-APEX-QUOTE',thread='APEX-1187',recipient='APEX')
    for did,kind,stage,actor,subject,body,links in extensions:
        message('MSG-'+did,stage,actor,subject,body,did,thread='C-VALIDATION' if stage==4 else 'OPERATIONS')
    for mid,stage,text in [
        ('MSG-MANAGER-IMPACT',1,'Which customer shipments are at risk, and what evidence supports the revised arrival date?'),
        ('MSG-MANAGER-COST',3,'Option A seems expensive. Why do we need to purchase 400 drives? Can we protect all customer deliveries at a lower upfront cost?'),
        ('MSG-MANAGER-PILOT',3,'Why cannot we use the 80 pilot-line drives? What approval and operational checks are needed?'),
        ('MSG-MANAGER-HYBRID',3,'Evaluate transferring 80 pilot-line drives and buying the remaining 40 from another qualified supplier. Keep all customer commitments protected.'),
        ('MSG-MANAGER-CONFIRM',4,'Can we meet all three contracted customer commitments, and what forecast work remains uncovered?'),
        ('MSG-MANAGER-APPROVE',5,'Approve scenario V2 Option C for preparing the 80-drive transfer and the 40-drive purchase draft. Nothing may be sent or released automatically.')]:
        message(mid,stage,'MANAGER','Decision case '+CASE,text,recipient='BRAIN',channel='chat')
    add('scenario_requests',3,'updated happy-case manager chat',request_id='REQ-V2',case_id=CASE,scenario_version=2,parent_version=1,transfer_qty=80,purchase_qty=40,objective='Protect contracted shipments, then minimize upfront spending; disclose forecast gap and inventory trade-offs.',actor_id='MANAGER')
    add('plan_approvals',5,'scripted manager role-play',approval_id='APPROVAL-V2',case_id=CASE,scenario_version=2,option_id='C',actor_id='MANAGER',approved_date='2026-10-26',authorized_purchase_cents=2200000,authorized_transfer_cents=200000,authority_limit_cents=25000000,authorization_scope='prepare_drafts_only')
    for did,kind,qty,sid,loc,unit,total,need in [('DRAFT-TRANSFER','internal_transfer',80,None,'PILOT',None,200000,'2026-10-28'),('DRAFT-PO','purchase_order',40,suppliers['Summit Precision Drives'],None,55000,2200000,'2026-10-29')]:
        add('action_drafts',6,'scripted expected action artifact',draft_id=did,case_id=CASE,scenario_version=2,approval_id='APPROVAL-V2',action_type=kind,part_number='HDR-200',qty=qty,supplier_id=sid,source_location_id=loc,destination_location_id='PRODUCTION',unit_price_cents=unit,total_cents=total,required_usable_date=need,status='draft',transmitted=False)
    for i,(stage,name,actor,text) in enumerate([
        (0,'potential_disruption','BRAIN','Supplier email conflicts with ERP. Impact analysis pending; PO is three calendar days overdue on October 23.'),
        (1,'case_validated','BRAIN','October 26 snapshot available; ERP PO is six calendar days overdue. Evaluate customer impact.'),
        (2,'initial_options_ready','BRAIN','Compare Apex with conditional pilot transfer under contract protection policy.'),
        (3,'scenario_requested','MANAGER','Create V2 within the same case; proposed 80 + 40 is not yet validated.'),
        (4,'evidence_released','BRAIN','Validate Supplier C and pilot release; rerun shared-capacity simulation.'),
        (5,'plan_approved','MANAGER','Scripted manager decision authorizes V2 draft preparation only.'),
        (6,'drafts_prepared','BRAIN','Two draft examples prepared; no transmission.'),
        (7,'execution_monitoring','BRAIN','Facilitator explicitly releases simulated execution evidence.')]):
        add('case_events',stage,'scripted role-play timeline',event_id=f'EV-{i:02}',case_id=CASE,event_type=name,event_date=STAGES[stage][1],scenario_version=2 if stage>=3 else 1,actor_id=actor,message=text)
    for mid,day,kind,entity,qty,status in [('MON-01','2026-10-27','simulated_manual_release','DRAFT-TRANSFER',80,'released_by_human_in_simulation'),('MON-02','2026-10-27','simulated_supplier_acceptance','DRAFT-PO',40,'accepted_in_simulation'),('MON-03','2026-10-28','simulated_transfer_receipt','REC-001',80,'usable'),('MON-04','2026-10-29','simulated_supplier_receipt','OFFER-C',40,'usable'),('MON-05','2026-10-29','forecast_follow_up','FC-001',130,'uncovered_in_horizon'),('MON-06','2026-10-29','primary_po_follow_up','PO-2026-1042',400,'still_open_due_november_10')]:
        add('monitoring_events',7,'held-back fictional execution feed',monitor_id=mid,case_id=CASE,scenario_version=2,event_date=day,event_type=kind,entity_id=entity,qty=qty,status=status)
    # Stable row order, including composite keys; exports preserve identical types.
    for table,rows in tables.items():
        keys=SCHEMA[table][0].split()
        rows.sort(key=lambda row:tuple(str(row[k]) for k in keys))
    return tables

def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def generate(output):
    output=Path(output)
    tables=build()
    policy=json.loads((ROOT/'context/policies.json').read_text())
    dictionary={}
    for table,rows in tables.items():
        columns=[]
        for name,typ in fields(table):
            base=typ.rstrip('?')
            desc=name.replace('_',' ')
            if name.endswith('_cents'): desc+='; integer USD cents, never floating point dollars'
            if name=='available_stage': desc='First role-play stage where this record is visible; inclusive'
            if name=='source_ref': desc='Provenance within supplied source pack or labeled authored extension'
            columns.append(dict(name=name,type=base,nullable=typ.endswith('?'),description=desc,snowflake_type={'str':'VARCHAR','int':'NUMBER(38,0)','bool':'BOOLEAN','date':'DATE'}[base]))
        dictionary[table]={'description':SCHEMA[table][2],'primary_key':['scenario_id']+SCHEMA[table][0].split(),'columns':columns,'foreign_keys':[{'columns':['scenario_id',col],'references_table':target,'references_columns':['scenario_id',key]} for t,col,target,key in FOREIGN_KEYS if t==table]}
        csv_path=output/'tables'/(table+'.csv'); csv_path.parent.mkdir(parents=True,exist_ok=True)
        with csv_path.open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=[n for n,_ in fields(table)],lineterminator='\n'); writer.writeheader()
            for row in rows:
                writer.writerow({k: '\\N' if v is None else str(v).lower() if isinstance(v,bool) else v for k,v in row.items()})
        (output/'tables'/(table+'.ndjson')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    write_json(output/'data_dictionary.json',dictionary)
    write_json(output/'catalog.json',{'scenario_id':SCENARIO,'case_id':CASE,'usage':'Full fixture catalog; use stage snapshots for agent inputs. Do not retrieve this entire catalog at initial stages.','tables':tables})
    for i,(name,day,description) in enumerate(STAGES):
        snapshot={'metadata':{'scenario_id':SCENARIO,'case_id':CASE,'schema_version':'2.0.0','stage':i,'stage_name':name,'as_of_date':day,'timezone':'America/Los_Angeles','synthetic':True,'description':description},'policies':policy,'tables':{t:[r for r in rows if r['available_stage']<=i] for t,rows in tables.items()}}
        write_json(output/'stages'/name/'dataset.json',snapshot)
        docs=output/'stages'/name/'documents'; docs.mkdir(parents=True,exist_ok=True)
        for document in snapshot['tables']['documents']:
            (docs/document['file_name']).write_text(document['content'],encoding='utf-8')
    files={str(p.relative_to(output)):digest(p.read_bytes()) for p in sorted(output.rglob('*')) if p.is_file() and p.name!='manifest.json'}
    write_json(output/'manifest.json',{'scenario_id':SCENARIO,'schema_version':'2.0.0','synthetic':True,'policy_sha256':digest((ROOT/'context/policies.json').read_bytes()),'row_counts':{t:len(r) for t,r in tables.items()},'stages':[{'index':i,'name':n,'as_of_date':d,'description':x} for i,(n,d,x) in enumerate(STAGES)],'source_hashes':{p.name:digest(p.read_bytes()) for p in sorted(SOURCE.iterdir()) if p.is_file()},'files':files,'database_status':'local_only_not_loaded'})
    return tables

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'generated')
    args=parser.parse_args()
    result=generate(args.output)
    print(f'Generated {sum(map(len,result.values()))} rows in {len(result)} tables at {args.output}')
