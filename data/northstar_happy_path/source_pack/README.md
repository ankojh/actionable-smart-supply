# NorthStar Robotics — Company Brain MVP Data Pack

## Overview

This pack is a complete demo story. **NorthStar Robotics** (fictional) builds the **NS-AMR-200** warehouse AMR. It is **Monday, October 26, 2026**, and the supply-chain manager asks one question: "Which shipments are at risk, and what should we do?"

- **The problem:** 250 robots must be built Oct 26 – Nov 6. Each needs 2 HDR-200 harmonic drive reducers, so 500 drives in total. The warehouse holds 120, leaving 380 uncovered. Pacific's PO-2026-1042 (400 drives) slipped from Oct 20 to Nov 10, and **that new date exists only in an email**. ERP just shows the PO as 6 days past due.
- **The impact:** the 120 drives go to SwiftLog (60 robots, ships Nov 5). BayShip (40) and NovaFab (20), both due Nov 6, are at risk: 60 robots and **$1,080,000** of gross revenue exposure.
- **Option A (recommended):** emergency purchase of 400 drives from Apex, received Oct 29, $194,400 cost, $29,600 unit-price premium. Every contract ships on time, even if Apex slips to Nov 4.
- **Option B:** 80 pilot-line drives protect BayShip, but NovaFab's 20 robots ($360,000) stay at risk.
- **How the decision is made:** the six-step policy in `company_policies.yaml`. Contract protection decides the ranking; cost is disclosed as a break-even. The manager approves, and the system only drafts the PO.

**Suggested reading order:** `SPEC.md` (full spec) → `demo_script.md` → `judge_qa.md`. Before building, run `python tests/verify_scenario.py` and confirm all 50 checks pass.

> All companies, people and figures are fictional sample data created for this demo.

---

## File inventory

| File | Contents |
|---|---|
| `SPEC.md` | Full scenario, schema guide, expected results, decision policy |
| `data/bom.csv` | 14 components of NS-AMR-200; HDR-200 = 2 per robot |
| `data/inventory.csv` | On-hand, allocated, safety stock; HDR-200 = 120 usable |
| `data/purchase_orders.csv` | 12 PO lines; PO-2026-1042 Open, ETA Oct 20 (past due) |
| `data/production_plan.csv` | PLAN-001 contracted 120, PLAN-002 forecast 130; 60 starts/day |
| `data/customer_orders.csv` | SO-001/002/003 contracted, FC-001 forecast |
| `data/recovery_inventory.csv` | REC-001 pilot line 80 usable; REC-002 not approved (distractor) |
| `docs/supplier_delay_email.md` | DOC-SUP-001 · Pacific delay notice, Oct 16 |
| `docs/backup_supplier_quote.md` | DOC-QUOTE-001 · Apex standard quote ADQ-2026-1187 |
| `docs/apex_emergency_confirmation.md` | DOC-APEX-002 · Apex emergency availability, Oct 26 |
| `docs/documents_index.csv` | The three documents with metadata, ready to load into Snowflake |
| `docs/optional/supplier_contract_excerpt.md` | Optional enrichment, not used in the core demo |
| `context/company_policies.yaml` | Company Brain: definitions, precedence, allocation, production, approval, decision policy |
| `tests/verify_scenario.py` | Reference calculation engine + 50 integrity checks |
| `demo_script.md` | 3-minute script with expected agent outputs and PO draft |
| `judge_qa.md` | 9 likely judge questions with answers |

### Built-in test traps (agents should handle these correctly)

- **PO-2026-1061** (CDC-050) has ERP status `Delayed` with an updated ETA. Stock already covers the plan, so there is no impact. Contrast: PO-2026-1042 is `Open` and the delay lives only in email.
- **PO-2026-0961** is an earlier, already-received HDR-200 PO. It must not count as inbound supply.
- **REC-002** has 16 drives but is not approved and requires inspection, so it must be excluded from Option B.
- **DOC-QUOTE-001** alone does not prove feasibility: standard lead time lands on Nov 10.

## Loading into Snowflake

Load order: `BOM` → `INVENTORY` → `PURCHASE_ORDERS` → `PRODUCTION_PLAN` → `CUSTOMER_ORDERS` → `RECOVERY_INVENTORY` → `SUPPLIER_DOCUMENTS`.

```sql
CREATE OR REPLACE FILE FORMAT NS_CSV
  TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"' NULL_IF = ('NULL', '');

CREATE OR REPLACE TABLE BOM (
  model VARCHAR, part_number VARCHAR, part_name VARCHAR,
  qty_per_unit INT, unit_of_measure VARCHAR);
CREATE OR REPLACE TABLE INVENTORY (
  part_number VARCHAR, part_name VARCHAR, on_hand_qty INT,
  allocated_qty INT, safety_stock_qty INT, snapshot_date DATE);
CREATE OR REPLACE TABLE PURCHASE_ORDERS (
  po_number VARCHAR, part_number VARCHAR, part_name VARCHAR, supplier VARCHAR,
  qty INT, unit_price_usd NUMBER(10,2), original_eta DATE, latest_eta DATE,
  status VARCHAR, open_qty INT);
CREATE OR REPLACE TABLE PRODUCTION_PLAN (
  plan_id VARCHAR, model VARCHAR, planned_build_qty INT, planned_start_date DATE,
  planned_completion_date DATE, daily_capacity_units INT,
  material_to_ship_days INT, priority VARCHAR);
CREATE OR REPLACE TABLE CUSTOMER_ORDERS (
  customer_order_id VARCHAR, customer VARCHAR, model VARCHAR, units INT,
  promised_ship_date DATE, contracted VARCHAR, unit_revenue_usd NUMBER(10,2), plan_id VARCHAR);
CREATE OR REPLACE TABLE RECOVERY_INVENTORY (
  recovery_id VARCHAR, part_number VARCHAR, source_type VARCHAR, source_name VARCHAR,
  recoverable_qty INT, available_date DATE, incremental_cost_usd NUMBER(10,2),
  approved_for_use VARCHAR, condition_status VARCHAR);
CREATE OR REPLACE TABLE SUPPLIER_DOCUMENTS (
  document_id VARCHAR, document_type VARCHAR, source VARCHAR, document_date DATE,
  related_po VARCHAR, related_part VARCHAR, file_name VARCHAR, content VARCHAR);

-- Upload files to a stage (Snowsight: Data → Add Data, or PUT), then for each table:
COPY INTO BOM FROM @NS_STAGE/bom.csv FILE_FORMAT = NS_CSV;
-- ... repeat for the other tables; SUPPLIER_DOCUMENTS ← docs/documents_index.csv
```

## Cortex Search

```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE SUPPLIER_DOCS_SEARCH
  ON content
  ATTRIBUTES document_id, document_type, source, related_po, related_part
  WAREHOUSE = COMPUTE_WH
  TARGET_LAG = '1 hour'
AS (
  SELECT document_id, document_type, source, document_date,
         related_po, related_part, content
  FROM SUPPLIER_DOCUMENTS
);
```

Key retrievals: the Nov 10 revised ETA in DOC-SUP-001, the $486 / MOQ 400 / 10-business-day terms in DOC-QUOTE-001, and the Oct 29 receipt and Oct 27 reservation in DOC-APEX-002. If indexing isn't ready by midday, query `SUPPLIER_DOCUMENTS` directly and mark retrieval `MOCKED`.

## Numbers cross-check (all files agree)

- 250 robots × 2 = **500 drives**; 500 − 120 = **380 uncovered**
- 120 drives → 60 robots → SO-001 SwiftLog; **60 contracted at risk** (BayShip 40 + NovaFab 20) = **$1,080,000**
- PO-2026-1042: 400 × $412 = **$164,800**; ERP ETA Oct 20 is **6 days past due** on Oct 26
- Apex: 400 × $486 = **$194,400**; premium 400 × $74 = **$29,600** (≈ 18%); standard lead time → **Nov 10**
- Option A: 520 drives, all 120 contracts ship by **Nov 2**; 20 drives left; slip tolerance **Nov 4**
- Option B: 200 drives → 100 robots; tie-break → BayShip protected, **NovaFab 20 = $360,000** at risk, ships Nov 12 (4 business days late)
- Break-even: **$29,600** = $1,480/robot = $7,400/late day = 8.2% of NovaFab's order

## Data integrity checklist

Run `python tests/verify_scenario.py` (needs `pyyaml`). It checks all of the above plus: no other open HDR-200 PO, only PO-2026-1042 past due, non-HDR parts cover 250 robots without breaching safety stock, unknown recovery cost stays null.

## Disclaimer

NorthStar Robotics, Pacific Motion Systems, Apex Drive Technologies, SwiftLog Logistics, BayShip Fulfillment, NovaFab Manufacturing and every other company, person and figure in this pack are **fictional sample data created for a hackathon demo**.
