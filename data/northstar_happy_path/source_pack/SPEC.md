# NorthStar Robotics MVP Spec

Scenario date: Monday, October 26, 2026 · Data is fictional · Authoritative policy file: `context/company_policies.yaml`

A delayed Pacific PO puts 60 contracted robots ($1,080,000 gross revenue exposure) at risk; an Apex emergency purchase of 400 drives removes all contracted exposure under the stated assumptions.

## 1. The scenario

### Company and product
NorthStar Robotics (fictional) builds autonomous mobile robots (AMRs) for logistics and warehousing customers. Product NS-AMR-200 has 14 key components; the critical one is the HDR-200 harmonic drive reducer, 2 per robot. Supply chain is the first use case for a shared Company Brain.

### Customers (planning horizon Oct 26 – Nov 6, 2026; $18,000 per robot)

| Order | Customer | Units | Promised ship | Value |
|---|---|---|---|---|
| SO-001 | SwiftLog Logistics | 60 | Thu Nov 5 | $1,080,000 |
| SO-002 | BayShip Fulfillment | 40 | Fri Nov 6 | $720,000 |
| SO-003 | NovaFab Manufacturing | 20 | Fri Nov 6 | $360,000 |
| FC-001 | Internal forecast | 130 | None (deferrable) | Not counted |

Contracted total: 120 robots, $2,160,000. Total plan: 250 robots.

### Suppliers
- **Pacific Motion Systems** (primary): PO-2026-1042, 400 × HDR-200 at $412 = $164,800, due Oct 20.
- **Apex Drive Technologies** (approved second source): standard quote + separate emergency confirmation.
- **Pilot line** (internal): 80 reusable HDR-200, excluded from warehouse inventory.

### What happened
- Oct 16: Pacific emails that the full 400 move to **Nov 10** (capacity constraints).
- ERP still shows ETA Oct 20, status `Open`, open qty 400 → 6 days past due. **ERP shows the PO is late; only the email shows how late.**
- Warehouse: 120 usable drives = 60 robots. Need 500 → **380 uncovered**.
- 120 drives go to SwiftLog (earliest promise). BayShip 40 + NovaFab 20 at risk = **$1,080,000 gross revenue exposure** (not realized loss).

### Production assumptions
- 10 business days, Mon–Fri; capacity **60 robot starts per business day**, shared by both plans.
- Day 0 = date drives become usable; robots ship Day 0 + 2 business days.
- Apex receipt = usable; pilot line `available_date` = usable.
- Other components available; no WIP or finished goods modeled; forecast builds deferrable.

### Option A — Apex emergency purchase (recommended)
- Standard quote: $486, MOQ 400, 10 business days → Nov 10. Cannot help.
- Emergency confirmation (Oct 26): 400 received Oct 29 if PO accepted by Oct 27.
- Cost $194,400; premium 400 × $74 = $29,600 (≈ 18%).
- Cash view: if PO-2026-1042 unchanged, NorthStar also still owes Pacific $164,800 for 400 drives on Nov 10.
- 520 drives → all 250 robots; 20 left. All contracts ship on time; exposure **$0**.
- Slip tolerance: Apex receipt as late as **Nov 4** still protects contracts.

| Start | Drives | Robots | Order | Shippable |
|---|---|---|---|---|
| Mon Oct 26 | 120 warehouse | 60 | SO-001 SwiftLog | Wed Oct 28 |
| Thu Oct 29 | 120 Apex | 60 | SO-002 BayShip 40 + SO-003 NovaFab 20 | Mon Nov 2 |
| Fri Oct 30 | 120 Apex | 60 | FC-001 | Tue Nov 3 |
| Mon Nov 2 | 120 Apex | 60 | FC-001 | Wed Nov 4 |
| Tue Nov 3 | 20 Apex | 10 | FC-001 | Thu Nov 5 |

### Option B — Pilot-line recovery
- 80 drives usable Oct 28 → 200 drives → 100 robots.
- Tie-breaker gives the remaining 40 robots to SO-002 BayShip (ships Oct 30).
- NovaFab 20 at risk: **$360,000**. It ships Nov 12 using Pacific's drives (4 business days late).
- Transfer cost unknown → stays null.

### Recommendation rules
Agent 2 recommends A and must state the $194,400 cost, $29,600 premium, cash view, evidence (DOC-SUP-001, DOC-QUOTE-001, DOC-APEX-002, PO-2026-1042), manager approval (within $250,000), the Oct 27 acceptance deadline, and the open Pacific PO warning. It must not claim Apex is cheapest overall, claim the premium is recoverable from Pacific, or net cost against revenue exposure.

### Demo flow
1. Agent 1 (Supply Risk & Impact) flags the past-due PO, retrieves the email, applies source precedence, traces PO → Inventory → BOM → Production → Orders, computes 380 drives and $1,080,000.
2. Agent 2 (Recovery & Decision) retrieves Apex documents, checks recovery inventory, applies the decision policy, compares A and B.
3. Manager approves Option A.
4. System drafts the Apex PO. Nothing is submitted or emailed.

### Three layers
- **Data (Snowflake):** 6 tables + indexed supplier documents.
- **Context (Company Brain):** relationships, definitions, precedence, policies, evidence references (YAML + SQL + Python + Cortex Search).
- **Decision (Claude agents):** two agents, Python orchestrator, Streamlit UI with approval.

Cortex Search retrieves documents; the Company Brain also defines how those facts relate to operational data and which rules govern decisions.

## 2. Schema guide

| File | Rows | Key fields / rules |
|---|---|---|
| `bom.csv` | 14 | `model, part_number, part_name, qty_per_unit, unit_of_measure` |
| `inventory.csv` | 14 | `on_hand_qty − allocated_qty` = available; HDR-200 = 120; safety stock is warn-only |
| `purchase_orders.csv` | 12 | PO-2026-1042 must stay `Open`, ETA Oct 20; no other open HDR-200 PO |
| `production_plan.csv` | 2 | `daily_capacity_units` = robot starts, shared; completion date is not the risk test |
| `customer_orders.csv` | 4 | `customer_order_id` is the same-date tie-breaker; forecast has null promise date |
| `recovery_inventory.csv` | 2 | Only `approved_for_use = Y` and `Usable` rows count; unknown cost stays null |
| `docs/documents_index.csv` | 3 | `document_id, document_type, source, document_date, related_po, related_part, file_name, content` |

| Document | Date | Key facts |
|---|---|---|
| DOC-SUP-001 Pacific delay email | Oct 16 | PO-2026-1042, 400 drives, Oct 20 → Nov 10, full quantity |
| DOC-QUOTE-001 Apex quote ADQ-2026-1187 | Oct 22 | $486, MOQ 400, 10 business days, valid to Oct 31, approved second source |
| DOC-APEX-002 Apex emergency confirmation | Oct 26 | 400 by Oct 29, $194,400 incl. expedite, reserved to Oct 27, released to production on receipt |

## 3. Expected results

| Measure | Baseline | Option A | Option B |
|---|---|---|---|
| HDR-200 usable in horizon | 120 | 520 | 200 |
| Robots buildable | 60 | 250 | 100 |
| Contracted on time | 60 | 120 | 100 |
| Contracted at risk | 60 | 0 | 20 (NovaFab) |
| Gross revenue exposure | $1,080,000 | $0 | $360,000 |
| Purchase cost | — | $194,400 | Unknown |
| Unit-price premium | — | $29,600 | — |
| HDR-200 left at horizon end | 0 | 20 | 0 |
| Latest Apex receipt still safe | — | Nov 4 | — |

Chain: 250 robots → 500 drives → 120 available → 380 uncovered → 60 contracted at risk → $1,080,000 → Apex 400 drives → $194,400 cost, $29,600 premium → $0 contracted exposure.

Rules: two agents only; SQL/Python computes every number, Claude interprets and explains; all purchasing needs human approval. Out of scope: supplier history, Sales threads, contract excerpts (optional file provided), multi-level BOM, transit modeling, optimization.

## 4. Decision policy

Six fixed steps. Contract protection decides the ranking; cost is shown as a break-even so the manager makes any cost-versus-customer trade-off.

1. **Evidence:** every number traces to a row or document ID; missing evidence → `insufficient_evidence`.
2. **Feasibility (pass/fail):** approved source; written confirmation (quote alone doesn't count); usable by `promised_ship_date − 2 business days`; capacity; reservation window open.
3. **Rank:** fewest contracted units at risk → fewest customers affected → lowest economic cost → lowest cash commitment.
4. **Economic check (advisory):** economic cost, cash commitment, break-even delay cost, unknowns tagged blocking / non-blocking, review flag if cost > 25% of protected exposure.
5. **Governance:** approval authority, supplier deadline, open primary PO follow-up, no automatic PO or email.
6. **Human decision:** approve, choose alternative, or defer; overrides need a one-line reason.

| Step | Option A | Option B | Result |
|---|---|---|---|
| Evidence | DOC-APEX-002, DOC-QUOTE-001, PO table | REC-001 | Both complete |
| Feasibility | Usable Oct 29; reservation to Oct 27 | Usable Oct 28 | Both pass |
| Protection | 0 at risk | 20 NovaFab, $360,000 | **A wins here** |
| Economic cost | $29,600 | Unknown (non-blocking) | Advisory |
| Cash | $194,400; Pacific $164,800 still due unless rescheduled | Unknown | Advisory |
| Break-even | NovaFab delay must cost > $29,600 | — | $1,480/robot, $7,400/day, 8.2% |
| Review flag | 8.2% | — | Below 25% |
| Governance | ≤ $250,000; accept by Oct 27 | — | Manager approves |

Optional later: add `customer_tier` and `late_penalty_per_day_usd`. Not for the demo unless agreed, since penalties near $7,400/day could flip the recommendation to B.
