# Shortage recovery through evidence investigation

This is a proposed workflow and an executable-data benchmark design, not an implemented application feature. It is a new fictional scenario with separate rules and identifiers. It does not modify NorthStar data, policies, storage or saved approvals.

Extension: [automatic recommendation and outcome evaluation](recommendation-and-outcomes.md) preserves the first updated workflow's three roles and evidence loop, replaces the benchmark's human selection with AI selection, and appends outcome calculation/reporting. It includes a separately labeled optional resilience analysis and an executed delay stress evaluation. The original case policy below remains cost-first. [Public delivery outcomes and a fair architecture comparison](public-outcome-validation.md) defines what real data can validate.

## Problem

When a supplier changes a delivery commitment, establish the time-phased shortage, investigate which recovery quantities are actually transferable or deliverable, and prepare the least-expenditure verified plan that protects contracted shipments. If evidence is incomplete, return the specific missing facts and a draft request rather than certify a candidate as feasible.

The product must distinguish stock on hand from unallocated stock, a matching title from an approved part, dispatch from usable receipt, and a catalog quantity from a written reservation. The optimizer runs only on supported facts.

## Included data and provenance

- [Initial case](initial_case.json): operational records, policies, catalogs and initial documents that exist at the initial evaluation time.
- [Future evidence events](future_events.json): simulated future supplier replies, injected by a test operator as dated events. These are not initial evidence and must not be exposed in advance to an agent under test.
- [Evaluation oracle](evaluation_oracle.json): expected results and independently enumerated feasible plans. Evaluator only; do not put this file in retrieval or an agent prompt.

All operational facts, part numbers, companies, prices and messages in these files are fictional. No real supplier was queried or contacted, no paid model was called, and no actual agent execution has been evaluated. Numeric calculations and the final plan were checked with calendar calculations, inventory balance and exhaustive enumeration over the declared quantity choices.

## Fully specified shortage

Scenario clock: Monday November 2, 2026, 08:00. The factory starts 30 robots at 09:00 each weekday, Monday November 2 through Friday November 6. Each robot requires two DRV-24-B revision B drivers. All other components are available. One shared line supports 30 starts per day. Ship date is the start date plus two Monday-Friday business days, with no holidays. These are explicit model assumptions.

Five contracted orders each require 30 robots, with promised ship dates November 4, 5, 6, 9 and 10. Production demand already represents those orders: do not add customer demand a second time.

Plant stock is 200 drivers, of which 20 belong to a separate service commitment. Thus 180 are usable for the five orders. Required drivers are 150 robots times two, or 300. The supplier has confirmed that the primary PO will arrive November 9, outside the relevant production window. It remains open.

| Production start | Required drivers | Baseline closing usable stock | Baseline impact |
| --- | --- | --- | --- |
| Mon Nov 2, 09:00 | 60 | 120 | Covered |
| Tue Nov 3, 09:00 | 60 | 60 | Covered |
| Wed Nov 4, 09:00 | 60 | 0 | Covered |
| Thu Nov 5, 09:00 | 60 | 0 | 60 drivers unmet |
| Fri Nov 6, 09:00 | 60 | 0 | 60 additional drivers unmet |

Total uncovered demand is 120 drivers, and 60 contracted robots are exposed without recovery. There is no invented revenue figure. Recovery must provide cumulative 60 by Thursday's start and cumulative 120 by Friday's start. A single blanket deadline would unnecessarily exclude useful Friday receipts.

## Evidence and candidate traps

**Warehouse B:** the summary shows 100 drivers on hand. A separate, initially accessible allocation ledger shows that 60 belong to another plant's existing commitment. Only 40 can transfer. The confirmed transfer reaches Plant A Thursday at 08:00 for a fixed $80 charge. A strong investigator can discover this before proposing any plan; the test must not force an incorrect 100-unit transfer.

**Standard Supply:** the catalog shows 80 revision B drivers at $12 each, MOQ 20 and multiples of 20, with $40 freight. The initial quote states Wednesday dispatch, not plant receipt. A later simulated reply confirms usable receipt Friday November 6 at 08:00 for up to 80 units. No Thursday commitment exists.

**Express Supply:** the catalog shows 80 revision B drivers at $16 each, MOQ/multiple 20 and $60 freight, with a quoted Thursday 08:00 receipt. A later reply confirms that only 20 can be reserved for that arrival. The catalog quantity is not a reservation.

**Budget Supply:** an attractive $8 listing concerns revision A. Its connector pinout differs and no engineering substitution approval exists. Approved distributor status is not permission to use the wrong revision. Exclude it unless genuine engineering approval is later supplied.

**Reserve Supply:** revision B, MOQ 100, multiples of 20, $13 each plus $100 freight. A later reply confirms up to 200 usable Thursday at 08:00. This is a valid but more expensive fallback; it prevents the benchmark from having only one feasible source combination.

All supplier reservations in the future replies expire Tuesday November 3 at 12:00. New recovery expenditure requires manager review; above $1,500 it requires escalation. No selection authorizes external execution.

## Three agent roles

1. **Operations investigator:** reconciles source allocations and commitments, identifies affected production/orders, and calls the inventory/calendar engine. Produces dated material requirements and cited source availability.
2. **Sourcing investigator:** searches exact-product catalog candidates, examines quote terms, and prepares specific evidence requests about reserved quantities and usable receipt. It has no send tool.
3. **Qualification reviewer:** investigates product revision, source qualification, receipt versus dispatch semantics, document scope and expiry. It returns precise unresolved predicates or rejection reasons, not generic criticism. Its result is not managerial approval.

The workflow host keeps the evidence snapshot, case state, budgets and human checkpoint. Deterministic code validates quantities and constraints, simulates schedules and chooses among verified candidates. The separate benchmark extension can select and simulate a recommendation automatically. Do not make another agent perform arithmetic or pretend the merge is an investigation.

## A concrete revision loop

Initial candidate discovery can surface an apparently cheap 100-unit warehouse transfer plus a 20-unit standard purchase. It is unverified, never executable, and invalid once allocations are checked. The operations investigator establishes a 40-unit transfer ceiling and sends a revised dated requirement: at least 20 external drivers Thursday, and 60 more Friday.

Sourcing may then consider the quoted 80-unit express receipt. The qualification reviewer requires a reservation for the exact revision and quantity. A simulated reply confirms only 20 by Thursday. This disproves an 80-unit express assumption without changing the underlying shortage.

Crucially, the reviewer should not label all standard delivery too late. Operations establishes that Thursday's 40 internal plus 20 express units already cover that start. Sourcing investigates whether the remaining 60 can be usable Friday and obtains the corresponding simulated confirmation. The verified plan is recomputed.

The ledger is accessible from the beginning. Future replies are new events, not documents artificially hidden to make a model err. An implementation may resolve several questions together and avoid the early candidates entirely; that should score well. Do not require a predetermined number of tool calls, mistakes or revisions.

When a reply has not arrived, the case must wait, request evidence, or choose another fully supported plan. It must not fabricate a response. A test operator can inject the supplied fictional reply; this is explicitly not a live supplier interaction.

## Verified final plan

| Action | Quantity | Usable receipt | New expenditure |
| --- | --- | --- | --- |
| Transfer unallocated warehouse stock | 40 | Thu Nov 5, 08:00 | $80 |
| Purchase Express revision B | 20 | Thu Nov 5, 08:00 | $320 plus $60 freight |
| Purchase Standard revision B | 60 | Fri Nov 6, 08:00 | $720 plus $40 freight |
| Total | 120 | Meets both production starts | $1,220 |

Thursday receives 60 drivers and starts 30 robots. Friday receives 60 and starts 30. The two orders ship November 9 and 10 as promised. All five contracted orders remain on time under the declared assumptions, and source allocations are preserved.

The $1,220 is new recovery expenditure: $1,040 of new purchased drivers, $100 supplier freight and $80 internal transport. It is not a premium-only measure, guaranteed profit or savings. The primary PO still represents a separate $2,400 obligation unless a human arranges a change. Do not assume cancellation or reimbursement.

A verified fallback is transfer 20 and buy 100 from Reserve: $1,480 total. It covers both starts if its confirmation is present, even if an urgent-source reply is missing. An evaluator must accept any plan optimal under the declared cost and tie rules, not only a fixed sentence or source ordering.

Independent enumeration found 2,442 feasible quantity combinations after the confirmations, including 23 within the manager's review limit. Feasible here means material and source constraints are satisfied; proposals still require human review.

## Actionable final artifact

Produce one draft transfer request and two draft purchase/booking requests, with exact part/revision, quantities, prices, freight, usable receipt times, reservation deadlines, source IDs and required manager review. Include the separate primary-PO follow-up. Every document remains DRAFT_NOT_SUBMITTED. Do not execute warehouse movements, send supplier messages or change customer commitments.

Missing evidence instead produces a specific draft request naming the quantity, revision and receipt requirement. Changed evidence invalidates selection and drafts and requires current reselection.

## Public data that can be added honestly

[WDC Products](https://webdatacommons.org/largescaleproductcorpus/wdc-products/) is a downloadable historical product-matching benchmark. Use its same-product/different-product labels to test identity matching separately. It does not establish the availability, qualification or delivery date of this fictional driver. Do not join unrelated product records to these part IDs.

[WDC PAVE](https://webdatacommons.org/structureddata/wdc-pave/) supplies annotated product attributes for extraction/normalization evaluation. Exact product identity and engineering interchangeability remain different tasks.

[Mouser](https://www.mouser.com/en/api-search/) documents availability, datasheets, MOQ, quantity multiples, lead time and price breaks. [DigiKey](https://developer.digikey.com/products/product-information-v4/productsearch) documents product details, quantity pricing, alternate packaging and substitutions. They require registered API access, which has not been established here. Catalog stock and suggested substitutions still do not prove a committed receipt or company engineering approval. Respect provider data-use terms when storing responses.

To add a live profile, first select a real overlapping component category, preserve actual manufacturer/distributor identifiers, capture permitted dated responses, and provide a separately labeled company BOM/approval manifest. Do not attach real vendor names to fictional quotes or silently fall back to the fixture on a live read failure.

The public [multiechelon automotive benchmark](https://data.mendeley.com/datasets/pr3sdy5vp3/1) can stress-test BOM, inventory and capacity calculations separately. It is not a source of this case's supplier messages. No claim is made that a fully public dataset provides a real company's complete ERP, reservations and current commercial confirmations.

## Evaluation

Compare a deterministic pipeline, a single agent with the same tools, the same specialist roles run sequentially, and the specialist workflow with genuinely independent investigations run concurrently. Give the single agent the same ability to parallelize tool calls. Keep evidence, budget and cache conditions comparable.

Score exact time-phased shortage, source-allocation conservation, revision matching, confirmed quantity/receipt/expiry, handling of unknowns, plan feasibility, cost against an independent oracle, and human-selection invalidation. Add candidate/evidence recall where the approved test set is complete. Measure latency and cost only alongside quality.

Specific tests: catalog 80 but reserved 20; on-hand 100 but free 40; Wednesday dispatch without receipt; cheap wrong revision; MOQ fallback; missing replies; expired reservations; Friday receipt that is useful after Thursday coverage; quote change after selection; no feasible full recovery. Do not reward extra conversation or penalize an agent for asking all necessary questions in one well-formed request.

Start with 30–40 reviewed cases, then generate 100–200 coherent variants. Hold out base product/scenario families rather than randomly splitting near-identical templates. Report paired latency/quality differences with uncertainty; no multi-agent advantage has yet been measured.

Statistical work includes entity/attribute accuracy, confidence calibration, feasible-plan rates and cost gaps across scenario families. This fixture does not support empirical supplier-delay probabilities. Future arrival distributions require real history or explicitly declared simulation assumptions.

## Snowflake and CoCo composition

Proposed tables: inventory snapshots, allocations, production/orders/BOM, source offers, documents, evidence claims, requests, event versions, plan revisions and human decisions. Snowflake SQL/Python computes requirements and validates candidates; Cortex Search can retrieve quote and technical text. CoCo/Claude supplies bounded specialist tool loops after actual account/tool availability is checked. The application host owns state, budgets and approval bindings.

The scenario can be replayed offline without credentials. A scripted replay tests the data and lifecycle, not model investigation quality. Actual model-backed evaluation requires separately authorized model execution. No such execution occurred while preparing this pack.
