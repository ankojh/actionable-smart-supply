import { useState } from "react";
import { Badge, Icon } from "./ui";

export const snowflakeSteps = {
  inbox: { title: "Risk alert", primary: "purchase_orders", resources: ["purchase_orders", "inventory", "snapshots"], description: "Live assessments read published purchase-order and inventory evidence from Snowflake. The inbox itself shows the local synthetic scenario; it does not run a background Snowflake monitor." },
  overview: { title: "Initial analysis", primary: "history", resources: ["snapshots", "documents", "search", "history"], description: "Live specialists read the governed snapshot, retrieve evidence with Cortex Search, and call the configured Claude model through Snowflake AI_COMPLETE. Qualification and planning checks run in this app." },
  investigate: { title: "Investigation", primary: "analyst", resources: ["analyst", "search", "documents", "history"], description: "Live Company Brain questions go to Cortex Analyst using the configured semantic view. Specialist evidence retrieval uses Cortex Search. Analyst SQL is proposed for review and is not executed by the app." },
  compare: { title: "Scenario comparison", primary: "audit", resources: ["audit", "reservations", "offers"], local: true, description: "The local planning engine simulates quantities, costs, and production dates against the Snowflake evidence snapshot. This step does not call an AI model. Live case checkpoints are queued for Snowflake audit export." },
  decision: { title: "Manager decision", primary: "audit", resources: ["audit", "policies", "snapshots", "history"], description: "The manager chooses the recovery. Before approval, the app checks the published Snowflake snapshot and source validity again. Live decision checkpoints are queued for audit export; Snowflake does not grant approval." },
  action: { title: "Action drafts", primary: "audit", resources: ["audit", "history"], local: true, description: "The app generates review-only purchase and transfer drafts locally. Live case checkpoints, including the decision and drafts, are queued for export to AUDIT_RECORDS. Offline cases can explicitly submit to the local simulator and track simulated execution here. Real supplier sending and transaction execution require external integrations." },
  impact: { title: "Inventory & business impact", primary: "inventory", resources: ["inventory", "allocations", "orders", "history"], description: "Live assessments read governed inventory, allocations, and order evidence from Snowflake. The app derives usable stock, shortages, and production impact from that snapshot." },
  recovery: { title: "Recovery sourcing", primary: "offers", resources: ["offers", "reservations", "search", "history"], description: "Live sourcing reads supplier offers and reservations from Snowflake, retrieves source evidence with Cortex Search, and uses Snowflake-hosted Claude when the chosen investigation approach uses model calls." },
  suppliers: { title: "Supplier qualification", primary: "reservations", resources: ["offers", "reservations", "documents", "history"], description: "Live qualification reads source records from Snowflake. The app independently verifies part, stock, reservation, price, and receipt constraints before a source can support a plan." },
  orders: { title: "Related production orders", primary: "orders", resources: ["orders", "purchase_orders", "history"], description: "Live order evidence comes from governed Snowflake views. The local engine evaluates the material and production calendar against these commitments." },
  evidence: { title: "Evidence & confirmations", primary: "documents", resources: ["documents", "snapshots", "reservations", "search"], description: "Live assessments load published, scoped source records from Snowflake and validate their hashes. A human reply does not automatically update authoritative supplier claims." },
  activity: { title: "Specialist activity", primary: "history", resources: ["history", "search", "audit"], description: "Find live AI_COMPLETE model calls and SQL reads in Snowflake Query History. Cortex Search and Analyst requests use REST APIs, so Query History is not a complete request log. The app’s activity log shows specialist and tool events." },
  outcomes: { title: "Outcome evaluation", primary: "outcomes", resources: ["outcomes", "realized"], description: "Live outcome replay reads realized events and exports results through a separate evaluator role in the evaluation schema. The planner cannot use these outcome records as evidence." },
};

export function SnowflakeLink({ workspace, resource, children, className = "", title }) {
  const target = workspace?.resources?.[resource];
  if (!target) return null;
  return <a className={`snowflake-link ${className}`} href={target.url} target="_blank" rel="noopener noreferrer" title={title || `${target.label}${target.object ? ` · ${target.object}` : ""}`}><Icon name="snow" size={13}/>{children || target.label}<Icon name="external" size={11}/></a>;
}

export default function SnowflakeActivity({ workspace, page, run }) {
  const [copied, setCopied] = useState("");
  const step = snowflakeSteps[page] || snowflakeSteps.overview;
  const offline = !run || run.mode !== "live";
  const configured = workspace?.status === "configured";
  const sqlString = (value) => `'${String(value).replaceAll("'", "''")}'`;
  const auditSql = configured && run ? `SELECT CREATED_AT, RECORD:kind::STRING AS KIND, RECORD:payload AS CASE_STATE\nFROM ${workspace.prefix}.AUDIT_RECORDS\nWHERE RECORD:run_id::STRING = ${sqlString(run.id)}\nORDER BY CREATED_AT DESC\nLIMIT 100;` : "";
  const snapshotSql = configured && run?.mode === "live" && run.snapshot ? `SELECT *\nFROM ${workspace.prefix}.SNAPSHOTS\nWHERE SNAPSHOT_ID = ${sqlString(run.snapshot.id)};` : "";
  const copy = async (text) => { try { await navigator.clipboard.writeText(text); setCopied("Copied"); } catch { setCopied("Select the SQL below to copy it."); } };

  return <section className="snowflake-activity" aria-label={`Snowflake visibility for ${step.title}`}>
    <div className="snowflake-activity-heading"><span className="snowflake-mark"><Icon name="snow" size={19}/></span><div><h2>Snowflake <span>/</span> {step.title}</h2><p>{offline ? "Reference links · This step is running locally" : step.local ? "Local processing · Snowflake evidence and audit" : "Live Snowflake integration"}</p></div><Badge tone={offline ? "neutral" : step.local ? "purple" : "blue"}>{offline ? "Offline case" : step.local ? "Local engine" : "Live mode"}</Badge></div>
    <p className="snowflake-description">{step.description}</p>
    {offline && <p className="snowflake-mode-note"><Icon name="info" size={13}/>This offline case sends no evidence, model requests, decisions, or drafts to Snowflake. Links open the corresponding configured cloud resources for reference.</p>}
    {run?.mode === "live" && run.execution === "deterministic" && <p className="snowflake-mode-note"><Icon name="info" size={13}/>This assessment uses the deterministic approach. Specialist analysis makes no AI_COMPLETE calls.</p>}
    {configured ? <>
      <div className="snowflake-resource-links">{step.resources.map((id) => <SnowflakeLink key={id} workspace={workspace} resource={id}/>)}</div>
      <details className="snowflake-inspect"><summary>What to look for in Snowflake<Icon name="chevron" size={12}/></summary><div className="snowflake-inspect-body">
        <dl><div><dt>Workspace</dt><dd>{workspace.prefix}{page === "outcomes" && "_EVAL"}</dd></div><div><dt>{page === "outcomes" ? "Evaluator role" : "Application role"}</dt><dd>{page === "outcomes" ? workspace.evaluator_role : workspace.role}</dd></div>{step.resources.includes("history") && <><div><dt>Query tag filter</dt><dd>{workspace.query_tag}</dd></div><div><dt>Model calls</dt><dd>SQL text contains AI_COMPLETE · {workspace.model}</dd></div></>}{run && <div><dt>Case ID</dt><dd>{run.id}</dd></div>}{run?.snapshot && <div><dt>Snapshot ID</dt><dd>{run.snapshot.id}{offline && " · Local snapshot"}</dd></div>}</dl>
        {step.resources.filter((id) => ["search", "analyst"].includes(id)).map((id) => <p key={id}>{workspace.resources[id].label}: <code>{workspace.resources[id].object}</code></p>)}
        {step.resources.includes("history") && <p>In Query History, filter by the query tag and application role above. These filters cover the application’s SQL activity across cases. Search and Analyst REST calls are separate from SQL history.</p>}
        {run?.mode === "live" && <p>Audit exports can be pending or delayed. The audit table is the place to verify what has actually arrived in Snowflake.</p>}
        {auditSql && <div className="snowflake-sql"><div><strong>{offline ? "Reference audit lookup · No cloud records for this offline case" : "Find this case’s exported checkpoints"}</strong><button className="text-button" onClick={() => copy(auditSql)}>Copy SQL</button></div><pre>{auditSql}</pre></div>}
        {snapshotSql && <div className="snowflake-sql"><div><strong>Find this evidence snapshot</strong><button className="text-button" onClick={() => copy(snapshotSql)}>Copy SQL</button></div><pre>{snapshotSql}</pre></div>}
        {auditSql && <p>No query is run automatically. Paste the read-only lookup into a Snowflake worksheet.</p>}
        {copied && <span className="snowflake-copy-status" role="status">{copied}</span>}
      </div></details>
    </> : <p className="snowflake-unavailable">{workspace?.note || "Snowflake console links are loading. If they stay unavailable, check the backend connection and Snowflake workspace configuration."}</p>}
  </section>;
}
