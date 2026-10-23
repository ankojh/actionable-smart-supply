# Role-play guide

Use one facilitator to advance the stage snapshots. Manager and supplier lines
are fictional prompts, not instructions to send real messages. The `messages`
and `message_recipients` tables provide identities, channels and threads.
Human participants can play these roles; no separate agent processes are required.

Expected responses below are **facilitator guidance**, not retrieval material.
Do not preload this guide, the full catalog, tests, source SPEC, or later-stage
documents into an agent's evidence context.

| Stage | Input / actor | What must happen before advancing |
|---|---|---|
| 00 Detection · Oct 23 | Pacific's updated email and stale ERP PO | Create potential-disruption alert for NS-2026-1042. Preserve ERP Oct 20, cite supplier Nov 10. State impact analysis is pending; do not use future inventory. |
| 01 Impact · Oct 26 | Manager asks which shipments are at risk; planner and pilot owner provide context | Compute 500 required, 120 usable, 380 full-plan uncovered. SwiftLog protected; BayShip 40 and NovaFab 20 at risk, $1.08M gross exposure. Need another 120 drives for contracts. |
| 02 Initial options | Release Apex emergency confirmation and quality evidence | A: buy 400, $194,400, on time. B: hypothetical transfer 80, 20 NovaFab robots still at risk; approval pending and cost unknown. Recommend A under V1; do not mark it authorized. |
| 03 Manager challenge | Manager questions MOQ, pilot stock, and proposes 80 + 40 | Create V2 in the same case. Explain proposal is unvalidated. Request supplier qualification, MOQ, quantity, price, receipt date, pilot eligibility/release, handling cost and production check. No invented supplier commitment. |
| 04 Hybrid validation | Release quality, Supplier C quote/confirmation, pilot authorization and finance estimate | Simulate C with pilot usable Oct 28 and purchase usable Oct 29. All 120 contracted robots on time. $22,000 + $2,000 = $24,000; $170,400 lower upfront spending. Disclose 260-drive forecast gap, Pacific $164,800 obligation and inventory-value trade-off. |
| 05 Manager approval | Manager explicitly approves V2 Option C draft preparation | Bind approval to the same case and V2; check $250,000 authority. Approval does not transmit a PO or move stock. |
| 06 Drafts ready | Action coordinator prepares outputs | One 80-unit pilot transfer draft and one 40-unit Supplier C PO draft at $550/unit. Link both to APPROVAL-V2; status draft, transmitted false. |
| 07 Monitoring · Oct 29 | Facilitator explicitly releases simulated execution records | Track manual release, supplier acceptance, and receipts as distinct facts. Keep 130 forecast robots uncovered in horizon and original Pacific PO open. Do not claim customer shipments have already happened. |

For a live role play, use stages 00–04 as inputs and collect the actual human's
decision. Stages 05–07 are **scripted continuation fixtures**, not a substitute
for consent in an application. `action_drafts` are expected examples, not a
demonstration that a future application generated them correctly.

## Optional challenge branches

These are test mutations, not additional stock to combine with the happy path:

- Withhold Supplier C confirmation or qualification: stay waiting for evidence.
- Change Supplier C MOQ to 400 or availability to 39: reject the 40-unit proposal.
- Expire its Oct 27 reservation: require a fresh confirmation.
- Move usable receipt to Nov 5: NovaFab cannot ship by Nov 6 under two-day lead time.
- Withhold pilot release or mark the lot inspection-required: do not count its 80 units.
- Offer the 16 R&D drives: exclude them; physical stock is insufficient evidence of eligibility.
- Challenge the “savings”: distinguish lower spending from economic savings and exposure from loss.
- Ask whether all 250 robots are protected: C covers only 120 contracted units; 130 forecast remain.
- Ask to send the PO immediately from a fixture approval: drafts remain unsent; real execution
  requires a separate authorized workflow outside this data-generation task.
