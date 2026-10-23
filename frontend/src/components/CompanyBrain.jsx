import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { SnowflakeLink } from "./SnowflakeActivity";
import { Badge, Button, Icon, date, money, planTitle } from "./ui";

const prompts = ["Why can’t we use existing inventory?", "Which recovery option costs the least?", "What deliveries are at risk?"];

function snapshotAnswer(question, run) {
  const operations = run.operations;
  const plan = run.plans?.find((p) => p.id === run.recommended_plan_id);
  const candidates = run.qualification?.candidates || run.sourcing?.candidates || [];
  const pending = run.evidence_requests?.filter((r) => !r.resolved) || [];
  if (!operations) return { text: "The specialists are still examining the evidence. Try again once the initial analysis is complete." };
  if (/inventory|stock|warehouse|existing|shortage|available/i.test(question)) return {
    text: `Current inventory cannot cover all production needs. ${operations.required_drivers} drivers are required, while ${operations.usable_drivers} are usable at the plant after allocations. That leaves ${operations.uncovered_drivers} drivers to recover. The warehouse holds ${operations.warehouse_on_hand}, but only ${operations.warehouse_transferable} are transferable without taking stock from another commitment.`,
    inventory: true, citations: operations.citations,
  };
  if (/cost|cheap|least|recommend|best|option|budget/i.test(question)) return plan ? {
    text: `The lowest-cost qualified plan is ${planTitle(plan)} for ${money(plan.expenditure_usd)} in incremental expenditure. It protects ${plan.on_time_units} on-time robots with ${plan.remaining_shortage} drivers left uncovered. The existing ${money(operations.original_po_obligation_usd)} primary PO obligation is separate.`,
    plan, citations: plan.actions.flatMap((a) => a.citations),
  } : { text: `A complete recommendation needs supplier confirmations. ${pending.length} source requests are open. Catalog inventory and quoted dates do not establish reserved stock or a usable receipt. Load the demo confirmations in this offline scenario, or review the source requests.`, citations: candidates.flatMap((c) => c.citations) };
  if (/deliver|order|risk|when|date|production/i.test(question)) return {
    text: `${operations.affected_orders.join(" and ")} need recovery to protect their production starts. ${operations.time_phased.filter((n) => n.cumulative_recovery > 0).map((n) => `By ${date(n.production_start)}, ${n.cumulative_recovery} recovery drivers are needed cumulatively.`).join(" ")} The primary supplier now confirms ${date(run.snapshot.case.primary_po.supplier_confirmed_eta)}, after these production starts.`, citations: operations.citations,
  };
  if (/simulat|scenario|combine|what if|transfer/i.test(question)) return { text: "Use New scenario to choose exact quantities from qualified sources. The planning engine checks receipt dates, allocations, order multiples, shared production capacity, and your spending authority. You can then compare the result and select a feasible plan.", scenario: true };
  return { text: "Offline answers use the current evidence snapshot and supported questions about inventory, delivery risk, costs, and recovery options. For a quantity-based what-if, select New scenario. Broader natural-language investigation is available in a live Snowflake assessment." };
}

export default function CompanyBrain({ run, workspace, onScenario, onSource, onCompare, expanded, onToggle }) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [asking, setAsking] = useState(false);
  const end = useRef(null);
  useEffect(() => { setMessages([]); setQuestion(""); }, [run.id, run.snapshot?.id]);
  useEffect(() => { if (messages.length) end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [messages]);
  const ask = async (value) => {
    const text = value.trim();
    if (!text || asking) return;
    setQuestion(""); setAsking(true);
    setMessages((current) => [...current, { role: "user", text }]);
    let answer;
    try {
      if (/^(try|what if|simulate|scenario)\b/i.test(text)) {
        const result = await api(`/graph/runs/${run.id}/scenario-proposal`, { snapshot_id: run.snapshot.id, checkpoint: run.checkpoint, question: text });
        answer = { text: "I prepared a scenario proposal. Review its quantities and assumptions before running the simulation.", proposal: result.proposal, note: result.note };
      } else if (run.mode === "live") {
        const result = await api(`/graph/runs/${run.id}/analyst`, { question: text });
        answer = { text: result.content?.filter((b) => b.type === "text").map((b) => b.text).join("\n\n") || result.note || "No text answer was returned.", blocks: result.content?.filter((b) => b.type === "sql"), note: result.note };
      } else answer = snapshotAnswer(text, run);
    } catch (error) { answer = { text: error.message, error: true }; }
    setMessages((current) => [...current, { role: "assistant", ...answer }]); setAsking(false);
  };
  const operations = run.operations;
  return <aside className={`company-brain ${expanded ? "expanded" : ""}`} aria-label="Company Brain assistant">
    <div className="brain-heading"><span className="brain-logo"><Icon name="brain" size={23}/></span><div><h2>Company Brain</h2><span><i/> Your supply chain copilot</span></div><button className="icon-button brain-toggle" onClick={onToggle} aria-label={expanded ? "Minimize assistant" : "Open assistant"}><Icon name={expanded ? "close" : "chevron"} size={17}/></button></div>
    <div className="brain-context"><Icon name="shield" size={13}/><span>Grounded in this case’s evidence</span><Badge tone={run.mode === "live" ? "green" : "blue"}>{run.mode === "live" ? "Live" : "Offline"}</Badge></div>
    <div className="brain-snowflake-links"><SnowflakeLink workspace={workspace} resource="analyst">Cortex Analyst</SnowflakeLink><SnowflakeLink workspace={workspace} resource="search">Cortex Search</SnowflakeLink></div>
    <div className="brain-messages" aria-live="polite" aria-busy={asking}>
      <div className="brain-welcome"><span className="ai-avatar"><Icon name="spark" size={18}/></span><h3>Let’s find a way forward.</h3><p>I can help you understand this risk, explore recovery options, and make an informed decision.</p><small>{run.mode === "offline" ? "Snapshot answers · Scripted demo" : "Snowflake Cortex Analyst"}</small></div>
      {!messages.length && <><div className="suggested-label">SUGGESTED QUESTIONS</div><div className="suggestions">{prompts.map((prompt) => <button key={prompt} disabled={asking} onClick={() => ask(prompt)}>{prompt}<Icon name="arrow" size={14}/></button>)}</div><div className="brain-tip"><Icon name="spark" size={17}/><div><strong>Explore a different approach</strong><p>Combine qualified sources and see the impact before you decide.</p><button className="text-button" onClick={onScenario} disabled={!run.plans?.length}>Create a scenario <Icon name="arrow" size={13}/></button></div></div></>}
      {messages.map((message, index) => <div key={index} className={`chat-message ${message.role}`}><span className={message.role === "user" ? "user-avatar" : "ai-avatar"}>{message.role === "user" ? "M" : <Icon name="spark" size={14}/>}</span><div className={`message-body ${message.error ? "message-error" : ""}`}><small>{message.role === "user" ? "You" : run.mode === "offline" ? "Company Brain · Snapshot answer" : "Company Brain"}</small><p>{message.text}</p>
        {message.inventory && <table className="chat-table"><caption>Driver availability</caption><tbody><tr><td>Plant usable</td><td>{operations.usable_drivers}</td></tr><tr><td>Warehouse transferable</td><td>{operations.warehouse_transferable}</td></tr><tr><td>Total required</td><td>{operations.required_drivers}</td></tr><tr className="shortage"><td>Plant shortage</td><td>{operations.uncovered_drivers}</td></tr></tbody></table>}
        {message.plan && <div className="chat-plan"><Badge tone="green">Feasible</Badge><strong>{money(message.plan.expenditure_usd)} recovery cost</strong><button className="text-button" onClick={onCompare}>Compare recovery options <Icon name="arrow" size={13}/></button></div>}
        {message.scenario && <Button onClick={onScenario} disabled={!run.plans?.length}><Icon name="plus" size={14}/>New scenario</Button>}
        {message.proposal && <Button onClick={() => onScenario(message.proposal)} disabled={run.status !== "WAITING_DECISION"}>Review proposed scenario</Button>}
        {message.blocks?.map((block, i) => <details key={i}><summary>Review proposed query · Not executed</summary><pre>{block.statement}</pre></details>)}
        {message.citations?.length > 0 && <div className="chat-sources">{[...new Set(message.citations)].slice(0, 3).map((id) => <button key={id} onClick={() => onSource(id)}><Icon name="file" size={11}/>{id}</button>)}</div>}
        {message.note && <small>{message.note}</small>}
      </div></div>)}
      {asking && <div className="thinking" role="status"><span className="spinner"/> Checking the current evidence…</div>}<div ref={end}/>
    </div>
    <div className="brain-compose"><Button className="new-scenario" onClick={onScenario} disabled={!run.plans?.length}><Icon name="plus" size={14}/>New scenario</Button><form onSubmit={(event) => { event.preventDefault(); ask(question); }}><textarea aria-label="Ask Company Brain" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a follow-up question…" rows={2} maxLength={2000} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(question); } }}/><button className="send-button" aria-label="Send question" disabled={asking || !question.trim()}><Icon name="send" size={17}/></button></form><small>{run.mode === "offline" ? "Answers use synthetic scenario evidence." : "Review evidence before making a decision."}</small></div>
  </aside>;
}
