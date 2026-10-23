# Demo Script — NorthStar Robotics Company Brain (3 minutes, Rev. 2)

Mark each segment `LIVE` or `MOCKED` on demo day to match what is actually built. The defaults below assume the must-have scope.

Scenario date: **Monday, October 26, 2026**. All figures reproduce from `tests/verify_scenario.py`.

---

## 0:00–0:30 — The question `LIVE`

**Presenter:**
> "It's Monday morning at NorthStar Robotics. Our supply-chain manager has 250 robots to build in the next two weeks, and three customers waiting. She asks one question."

**Manager types:**
> "Which upcoming customer shipments are at risk due to supplier delays, and what should we do?"

---

## 0:30–1:10 — Agent 1: Supply Risk & Impact `LIVE`

**What happens on screen:** Agent 1 queries Snowflake, flags the overdue PO, retrieves the email through Cortex Search, and traces the impact.

**Expected Agent 1 output:**

```
ERP CHECK
  PO-2026-1042 · Pacific Motion Systems · 400 × HDR-200 · status Open
  ERP latest ETA 2026-10-20 → 6 days past due, open qty 400. New date unknown in ERP.

EVIDENCE  DOC-SUP-001 (Pacific email, Oct 16)
  "delayed from October 20 to November 10, 2026 ... applies to the full outstanding quantity"
  Source precedence → planning ETA = 2026-11-10 (after horizon end Nov 6). ERP record preserved; discrepancy logged.

IMPACT  (PO → Inventory → BOM → Production → Customer Orders)
  Plan 250 robots × 2 HDR-200 = 500 drives · usable 120 · uncovered 380
  120 drives → 60 robots → allocated to SO-001 SwiftLog (earliest promise, Nov 5)
  AT RISK: SO-002 BayShip 40 (Nov 6) · SO-003 NovaFab 20 (Nov 6)
  60 contracted robots · $1,080,000 gross revenue exposure (not realized loss)
  Warning: HDR-200 will fall below safety stock (100). Warn only.
```

**Presenter:**
> "ERP knew the PO was late. Only the email knew *how* late. The Company Brain connects the two and follows it all the way to customer revenue."

---

## 1:10–2:10 — Agent 2: Recovery & Decision `LIVE`

**What happens on screen:** Agent 2 retrieves the Apex quote and emergency confirmation, checks pilot-line inventory, and applies the six-step decision policy from `company_policies.yaml`.

**Expected Agent 2 decision packet:**

| Field | Option A — Apex emergency | Option B — Pilot line |
|---|---|---|
| Evidence | DOC-QUOTE-001, DOC-APEX-002 | REC-001 (REC-002 excluded: not approved, requires inspection) |
| Feasibility | Pass: approved source, written confirmation, receipt Oct 29, reservation to Oct 27 | Pass: approved, usable Oct 28 |
| Contracted at risk | 0 · $0 | 20 NovaFab · $360,000 |
| Economic cost | $29,600 price premium | Transfer cost unknown (non-blocking) |
| Cash | $194,400 now; Pacific $164,800 still due Nov 10 unless rescheduled | Unknown |
| Break-even | NovaFab's 4-business-day delay must cost > $29,600 ($1,480/robot, $7,400/day, 8.2% of order) | — |
| Slip tolerance | Contracts still safe if Apex receipt slips to Nov 4 | — |

> **Recommendation: Option A** — decided at step 3 (contract protection). Cost is advisory, below the 25% review flag.
> Standard Apex lead time (Oct 27 + 10 business days = Nov 10) would not help; only the emergency confirmation makes A feasible.
> Approval: manager, $194,400 ≤ $250,000 limit. PO must be accepted by Apex by **Oct 27**.
> Follow-up: review PO-2026-1042 (reschedule or reduce) to avoid 400 surplus drives.

**Presenter:**
> "Notice what the agent does *not* say. It doesn't say 'spend $194K to save $360K' — cost and revenue exposure are different things. It shows the manager the break-even and lets her decide."

---

## 2:10–2:40 — Human approval + governed action `LIVE` (PO draft) / `MOCKED` (no ERP write)

Manager clicks **Approve Option A**. The system produces a draft only.

```
DRAFT — NOT SUBMITTED
Purchase Order        PO-2026-DRAFT-0001
Supplier              Apex Drive Technologies
Reference             Quote ADQ-2026-1187 · Emergency confirmation DOC-APEX-002
Item                  HDR-200 Harmonic Drive Reducer (Spec Rev C)
Quantity              400 ea
Unit price            $486.00 (expedited delivery included)
Total                 $194,400.00
Requested receipt     2026-10-29, DAP Fremont, CA
Payment               Net 30 from delivery
Acceptance needed by  2026-10-27 (reservation expires)
Approved by           Supply Chain Manager (within $250,000 limit)
Linked follow-up      Review PO-2026-1042 (Pacific, 400 ea, ETA Nov 10)
```

**Presenter:**
> "The agent drafts. A human approves. Nothing is sent automatically."

---

## 2:40–3:00 — Closing pitch `LIVE`

> "Two agents, one Company Brain. Agent 1 and Agent 2 never pass rules to each other — they read the same definitions, the same source-precedence rule, the same decision policy. Swap the YAML for another company's rules and the agents don't change. That's what turns a chatbot over your data into an organization that can decide."

---

## Fallbacks

- **Cortex Search not working:** query `SUPPLIER_DOCUMENTS` by `document_id` / `related_po`; mark Agent 1 retrieval `MOCKED`.
- **Agent output unstable:** numbers come from the Python engine, so show the engine's table and let Claude explain it.
- **Judge asks "what if Apex is late?":** slip tolerance — contracts safe through a Nov 4 receipt.
