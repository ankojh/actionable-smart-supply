# Source decisions and explicit fictional assumptions

Read on October 23, 2026 using the user's authorized Gmail Chrome profile.

## Precedence

1. [Updated Happy Case Scenario](https://app.notion.com/p/Updated-Happy-Case-Scenario-402be21f91f980c8beedd85cb049cbf9),
   [Final Happy Case](https://app.notion.com/p/NorthStar-Robotics-Final-Happy-Case-402be21f91f98020b173df42a4542244),
   and [Manager demo chat](https://app.notion.com/p/Manager-Company-Brain-Demo-Chat-402be21f91f9804ea6a7f1847c0115b0)
   govern the final hybrid flow, manager interaction, new supplier and draft boundaries.
2. The separately supplied `DOC-SUP-001_updated_supplier_email.md` replaces the
   ZIP's version of that same document ID in all active stage exports. The original
   is preserved only under `source_pack`, never indexed alongside its replacement.
3. ZIP operational CSVs and production rules supply the numerical baseline.
4. The [overview](https://app.notion.com/p/Company-Brain-for-Supply-Chain-Hackathon-MVP-Overview-400be21f91f980c9937eeed790dbe65f)
   provides the data relationships and initial A/B analysis. Its older Apex-only
   ending and two-agent implementation sketch do not override the updated happy case.

Source documents are evidence for this task, not instructions to execute cloud
loading commands or send communications. The supplied verifier was inspected as
reference; the new checks independently test the new data contract.

## Reconciliations

| Issue | Resolution |
|---|---|
| October 23 detection vs October 26 evaluation | Stage 0 detects the email/ERP conflict on Oct 23 (3 calendar days overdue). Stage 1 receives the Oct 26 inventory/planning snapshot (6 days overdue). No Oct 26 stock is exposed on Oct 23. |
| ZIP says pilot stock approved for use | Treat that as technical eligibility only. The updated scenario requires separate operational release and manager approval. Original CSV is preserved; active `recovery_inventory` uses explicit eligibility/release fields. |
| Pilot line stock and 120 warehouse stock | Separate physical pools. The 80 pilot units are never added to warehouse opening stock or deducted from its 120 units. |
| Original policy ranks economics differently | V1 protects contracts. V2 records the manager's explicit objective to protect contracts then reduce upfront expenditure. Economic savings are not inferred. |
| Unknown pilot cost vs $2,000 | V1 remains null. V2 introduces a separately cited fictional $2,000 estimate, not a supplier invoice. |
| Old overview approves A | A is only the initial recommendation. Final happy path approves C in V2 within case NS-2026-1042. |
| Approval vs execution | Pilot owner authorizes release eligibility; manager authorizes draft preparation. Final human transmission/execution is distinct and appears only in a separately released simulated monitoring feed. |
| Net 30 vs “immediate cash” | Preserve Net 30 in quotes. The demo's $24,000/$194,400 comparison means stated upfront purchase/handling commitment, not an actual cash-payment date. |

## Newly authored details required to make V2 testable

The Notion specification leaves Supplier C's identity, MOQ within a range, dates,
and supporting confirmations to the engineering dataset. These additions are
fictional fixtures, not claims copied from the original ZIP:

- Supplier C is **Summit Precision Drives**, represented by Owen Patel. MOQ 40,
  confirmed quantity 40, $550 each; quote `SPD-2026-0040`.
- Qualification `AVL-HDR-200-03`: HDR-200 Rev C, ratio 100:1; valid Jan 1–Dec 31,
  2026. Quality evidence becomes available during V2 validation.
- Supplier C receipt and usable date: **Oct 29**; acceptance deadline **Oct 27**,
  end of day Los Angeles. Delivery and inspection included, no additional fee.
- Pilot owner Elena Brooks grants conditional operational release authorization
  Oct 26, release date **Oct 27**, usable in production **Oct 28**. Pilot work can
  wait until after Nov 10 without affecting another contracted customer.
- Manager Maya Chen, buyer Alex Morgan, planner Riley Park, quality Jordan Lee,
  and finance Sam Taylor are authored role-play identities. Daniel Reyes and
  Priya Natarajan retain the supplied supplier roles. All addresses use `.example`.
- Explicit calendar: Mon–Fri, no modeled holidays, two business days to shipment.
  Need-by dates derive from promised shipment minus this lead time, not the
  production-plan completion field.
- Future monitoring entries are scripted examples: manual release/acceptance
  Oct 27, pilot receipt Oct 28, Supplier C receipt Oct 29. They are not actual
  executions or proof of agent performance. Customer deliveries are still projected.

The optional supplier contract excerpt stays outside active evidence. Cost
recovery, contractual damages, taxes and economic inventory valuation are not
assumed. Original source files remain unchanged for provenance.
