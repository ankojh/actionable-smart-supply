# Coverage map

| Requirement / trap | Data / evidence | Validation |
|---|---|---|
| Email changes ETA while ERP stays Open | PO-2026-1042 + exact replacement DOC-SUP-001 | ERP date/status preservation and source-body checks |
| Detection precedes full analysis | 8 staged snapshots, dated inventory/documents | No future stock/documents or broken stage links |
| Supplier → PO → component → BOM → plans → customers | Supplier/part/product masters and operational foreign keys | Keys and references in every stage |
| Full BOM, not just the constrained part | 14 BOM and inventory rows | Scheduler consumes all parts; low-LiDAR mutation limits production |
| Allocation tie break | SO-002 and SO-003 both due Nov 6 | B protects BayShip first, leaves NovaFab 20 |
| Shared capacity and weekends | Explicit 29-day calendar, single 60-start line | Daily capacity, Thu start → Mon shipment, Nov 4/5 need-by boundary |
| Forecast vs contracted requirements | PLAN-001/002, FC-001 | C leaves 130 forecast units / 260 drives uncovered |
| Received PO must not become supply | PO-2026-0961, open_qty 0 | Only one open HDR PO; no double-counting receipt/history |
| Delayed status need not mean customer risk | CDC-050 PO-2026-1061 | Opening non-HDR BOM stock covers the entire plan |
| Quote alone does not confirm delivery | DOC-QUOTE-001 vs DOC-APEX-002 | Standard offer has null confirmed date; stage-based confirmation |
| Technically eligible does not mean released | REC-001 vs REL-001 | Pilot absent from opening usable stock and initial authorization |
| Quarantined/inspection stock excluded | REC-002, 16 drives | Condition/eligibility rejection mutation |
| Realistic supplier role play | Daniel, Priya, Owen; request/reply and document links | Identity, message, reply and recipient foreign keys |
| V2 supplier validations | DOC-QUAL-C, DOC-QUOTE-C, DOC-CONF-C | MOQ, qualification, quantity, expiry and late-arrival negative tests |
| Cost uncertainty | V1 null, DOC-COST-001 V2 estimate | Null survives CSV/JSON; $24,000 reproduced |
| Reversible same-case revision | REQ-V2, versioned messages and events | All stages use NS-2026-1042; V2 introduced at manager challenge |
| Human approval and draft boundary | APPROVAL-V2 and two action draft examples | Stage gates; transmitted false |
| Future evidence isolation | Monitoring records only in stage 07 | Stage equality and future-date checks |
| Repeatable later migration | Dictionary, CSV/NDJSON, manifest | Round-trip types/nulls, checksums, two independent regeneration directories |
| Previous data fully preserved | Archive manifest of 184 files | Byte-for-byte archive hash checks |

## Limits

This is one substantive happy path with negative-control tests, not a volume or
performance benchmark. No real supplier history or probabilities are invented.
The scheduler is a local fixture oracle, not a general optimizer. Production
agents must still extract the documents and calculate results independently;
passing these checks does not establish application, retrieval or model quality.
