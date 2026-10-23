# Judge Q&A Card (Rev. 2)

**1. How is this different from Snowflake Intelligence or a standard RAG app?**
Snowflake gives us governed data and retrieval. RAG finds the email. The Company Brain adds what retrieval can't: how the email relates to PO-2026-1042, which source wins when ERP and email disagree, how drives map to robots to customers, and the decision policy both agents share. Change the YAML and both agents behave differently, with no code change.

**2. Where does the data come from?**
All synthetic. Shaped like real ERP extracts and supplier documents to reproduce real fragmentation: the ERP shows the PO as overdue, only an email holds the new date.

**3. What's the business value?**
The MVP surfaces $1,080,000 of gross revenue exposure and produces an evidence-backed recovery decision in minutes. We haven't measured time savings against a real team, so we don't claim a number.

**4. What did you build versus mock?**
Live: Snowflake tables and queries, Cortex Search over supplier documents, two Claude agents, the Python calculation engine, the approval step and PO draft. Mocked: any write-back to ERP and any email to suppliers (deliberately not automated). Update this answer on demo day to match the build.

**5. What would production need?**
Live ERP and email integrations, richer production scheduling, authorization and approval workflows, audit logs, data-freshness checks, agent evaluations and monitoring.

**6. What if Apex is late too?**
The remaining 60 contracted robots fit in one day's capacity. Contracts stay protected if Apex receipt slips as late as Wednesday, November 4; only forecast builds move.

**7. Isn't the $29,600 premium understated?**
It's the unit-price premium, true only if the Pacific PO is rescheduled or reduced. If PO-2026-1042 stays as is, NorthStar commits $194,400 now and still owes Pacific $164,800 for 400 drives arriving November 10. The agent reports both numbers and flags that PO for review.

**8. Why not just compare cost against the $360,000 at risk?**
Because one is cost and the other is gross revenue, not profit. The agent shows the break-even instead: NovaFab's 4-day delay would need to cost more than $29,600 ($7,400 per day) for Option A to pay off. The manager makes that call.

**9. Is this an industry-standard policy?**
The pieces are common practice: contracted before forecast, earliest-date allocation, approved vendors only, delegation of authority. "Protect contracts first" is NorthStar's policy, simplified for the demo. Real teams add customer tiers and late penalties. Those are just more fields in the Company Brain.
