# Public delivery outcomes without changing the original graph

Review date: October 23, 2026. This is a research/evaluation design, not a dataset import, trained model, executed agent comparison or application integration. The first updated workflow's Operations investigator, Sourcing investigator and Qualification reviewer are preserved in [the corrected augmentation](recommendation-and-outcomes.md). Default selection remains the original cost-first objective. The older PDF's four-role diagram is not the reference for this augmentation.

## Recommendation

Use SCMS/USAID shipment history as the closest upstream procurement outcome benchmark, with Olist as a broader commerce replication dataset. Keep the fictional robot shortage as a separately labeled operational case. Neither dataset supplies its BOM, warehouse commitments, alternative quotes or supplier reservations. Do not join historical medicines or consumer shipments to fictional robot suppliers and call the resulting forecast validated.

The data supports tests of delivery forecasting, historical evidence retrieval and correct use of uncertainty. It does not on its own identify the outcome of a new, unexecuted recovery action. Test recovery quality separately in a simulator, and label that result accordingly. Multiple agents may improve evidence handling or efficiency; a dataset cannot establish that advantage before a controlled comparison is run.

## Datasets and access evidence

### SCMS / USAID Supply Chain Shipment Pricing

[DataRobot's official worked example](https://docs.datarobot.com/11.1/en/docs/get-started/gs-dr5/biz-accelerators/late-ship.html) identifies the public PEPFAR shipment dataset, links its [Kaggle distribution](https://www.kaggle.com/datasets/divyeshardeshana/supply-chain-shipment-pricing-data), and defines the label using actual versus scheduled delivery. It describes approximately 10,320 records in its prepared example, which is not a freshly verified count of the raw file.

An [accessible raw CSV mirror](https://raw.githubusercontent.com/jrcinco/supply-chain-shipment-price-data/master/SCMS_Delivery_History_Dataset.csv) was opened in this review. Its header and sample records contain vendor, country, shipment mode, PO and ASN references, product/pack attributes, quantity/value and scheduled, delivered-to-client and recorded delivery dates. For example, record ID 130 shows scheduled August 13, 2007 and delivered August 21, 2007. This is an observed recorded shipment, not the outcome of our proposed recovery plan.

The original USAID CSV endpoint could not be retrieved during this review. The accessible mirror is a third-party copy, not independently reconciled to an agency checksum. Before importing it, pin its version/hash and provenance, verify the distribution terms and audit the raw file. Do not promise a working original-agency download.

Necessary audits include true row grain, shared ASN/PO dependencies, revised versus original scheduled dates, missing PO dates, date chronology, shipment-level costs repeated across line items, and nonnumeric freight values such as costs included in commodity price or references to another ASN. These are unknown or structured states, not zero. Shipment quantities are not an inventory ledger or customer demand series.

### Olist Brazilian E-Commerce

The [publisher's dataset card](https://www.kaggle.com/olistbr/brazilian-ecommerce/home/) describes about 100,000 anonymized real commercial orders from 2016-2018 and linked order, item, seller, customer, product, freight and review data. It warns that one order may contain items from different sellers. Its license is CC BY-NC-SA 4.0; preserve the stated usage conditions.

This is a good replication setting for order delivery risk. It is not upstream component procurement, and anonymized product IDs are not engineering part numbers. It lacks the robot case's stock, reservations, BOM and recovery decisions. Prefer single-seller orders for the initial seller-level study; an order-level receipt timestamp must not be presented as a separate observed receipt for each seller or item. Treat reviews as post-outcome evidence, never advance signals.

### DataCo Smart Supply Chain

The [researchers' Mendeley V5 release](https://data.mendeley.com/datasets/8gx2fvg2k6/5) lists a 91.5 MB structured CSV, a data dictionary and clickstream logs under CC BY 4.0. The [published dictionary](https://data.mendeley.com/datasets/8gx2fvg2k6/1/files/29dc7b05-dda6-4834-8354-5b5cc44430df) defines actual shipping days, scheduled shipping days, delivery status and a late-delivery flag. The dictionary/version relationship must be checked against the downloaded V5 columns.

This is an alternative broad commerce benchmark, not a complete manufacturing shortage-recovery dataset. Actual days, final delivery status and the late flag are outcomes, not pre-decision predictors. Pin the release, audit order-line duplicates and confirm timestamp semantics before evaluating. The repository metadata and indexed dictionary were reviewed; the full CSV was not downloaded or audited here.

## What actual delivery outcomes can validate

Take a historical booked shipment and define a decision time before delivery. Build features using only evidence known then, forecast its late probability or receipt interval, freeze the prediction, and compare it with the recorded outcome. History aggregates may include only outcomes already observable at that time. A final database export is not a versioned event log; uncertain field availability or later edits must be documented, not reconstructed as known facts.

Evaluate a calendar/vendor/lane baseline, logistic regression and a stronger tabular model with chronological train/validation/test periods and an outcome-maturation gap. Tune only on training/validation. Compute Brier score or log loss, calibration, PR-AUC for late deliveries, and precision/recall at a fixed investigation capacity. Compare arrival quantiles with observed timing using interval coverage and quantile loss. Audit subgroup support; sparse vendor/lane samples need pooling or abstention. Accuracy alone can hide a majority-class predictor.

This verifies statements such as 'the top 50 flagged booked shipments contain more actual late deliveries than the baseline.' It does not verify 'switching these shipments to air would have prevented 80% of delays.' Prediction explanations describe associations unless a causal design establishes otherwise.

## What remains unobserved

For a recorded shipment through supplier A, its receipt under supplier B, a new transfer or an expedite is unobserved. A different historical supplier-B shipment does not become that counterfactual. The cases may differ in demand, route, product, date and urgency. The public datasets also do not establish that B had available approved stock at the decision time.

Use simulated recovery outcomes, clearly labeled, until intervention evidence exists. A stronger operational claim requires a prospective trial or suitable logged intervention data with treatment-selection information, confounder control and overlap. Off-policy estimates require assumptions and support; do not infer validity merely because a shipping-mode column exists. [Counterfactual Risk Minimization](https://arxiv.org/abs/1502.02362) explains the logged-feedback problem and propensity-based approach; these public tables have not been shown to satisfy its requirements.

## Comparing the first three-role workflow to one agent

Use four conditions: deterministic SQL/model/optimizer pipeline; one Claude agent with all the same tools; the three specialist roles run sequentially; the first workflow's concurrent Operations/Sourcing investigation followed by Qualification review. Match Claude model/version, total model/tool budget, data access, retrieval corpus, optimizer, risk model and deadlines. Allow the single agent multiple steps and concurrent tool calls; do not force it into a one-shot prompt. Alternate run order and specify cache conditions.

Use two evaluation tracks and keep their claims separate:

1. **Real historical track:** score evidence retrieval and late-risk investigation against actual booked-shipment outcomes. Primary metric can be recall of genuinely late shipments among a fixed-size investigation queue, with unsupported-claim rate and precision reported alongside it. Supply the same risk-model tool to every architecture; improved prediction from a different model is not evidence of better agent coordination. Inventory/exposure/reallocation fields absent from the source remain unavailable, so this track does not validate the whole business workflow.
2. **Operational recovery track:** augment with explicitly synthetic, reviewed BOM, inventory, allocation, quote and evidence-event records. Test the first workflow's ability to find missing predicates, revise candidates, preserve commitments, produce feasible plans and choose the correct cost under the same objective. Score downstream service only as simulated. Synthetic evidence packets are controlled tests, not real supplier correspondence. Include no-LLM baseline and easy cases; do not manufacture ambiguity to force agent wins.

Hold out whole business/scenario families and use real chronological holdout for delivery predictions. Match both architectures to the same case and outcome events, and freeze recommendations before evaluation outcomes are revealed. Repeated seeds, line items in one shipment and template variants are not independent business cases.

Predeclare a primary endpoint, a meaningful improvement threshold and allowable extra model cost. Run a pilot to estimate paired variation, then choose sample size for the intended comparison. Report case/family counts and paired differences with 95% uncertainty intervals; cluster resampling by shipment/scenario family, and time block when needed. Use calibration and service metrics with their limits, not an LLM judge or a composite score that can hide infeasible plans. Report feasibility failures and valid abstentions separately. Include cost and p50/p95 latency.

A positive result supports a bounded claim about the tested data, task and budget. If multi-agent quality is unchanged but latency improves, claim an efficiency improvement. If a single agent or deterministic pipeline is as good or better, report that result. [Controlled agent-scaling research](https://arxiv.org/abs/2512.08296) similarly finds that coordination benefits depend on task structure and overhead; it does not establish a benefit for this supply-chain graph.

## Snowflake and current status

The public history can become a separately scoped Snowflake history table, with a historical risk model exposed as a shared SQL/Python tool. Keep observed outcomes, generated scenario facts, forecast outputs, plans and evaluation scores in separate versioned tables. Both architectures use the same tools and data. Existing Claude inference/CoCo authentication boundaries remain unchanged; this design does not claim a new connection works.

This review checked public documentation and an accessible SCMS raw-data mirror. No dataset was imported, no new forecast was fitted, and no paid model, Snowflake service or agent architecture trial was run. The earlier $1,220/$1,480 outcomes remain fictional-case simulation results; public delivery history does not retrospectively validate those dollar figures or recovery probabilities.
