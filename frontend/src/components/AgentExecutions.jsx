import { Badge, Icon, SectionHeading, words } from "./ui";

export default function AgentExecutions({ run, workspace }) {
  if (run.mode !== "live" || run.execution === "deterministic") return null;
  const executions = Object.entries(run.agent_executions || {}).flatMap(([role, entries]) => entries.map(entry => ({ ...entry, role })));
  const directory = workspace?.resources?.agents?.url || (workspace?.base_url ? `${workspace.base_url}/#/agent-studio` : null);
  return <section className="card managed-agent-executions" aria-label="Snowflake Agent executions">
    <SectionHeading title="Snowflake Agent executions" subtitle="Open each agent’s page and execution history in Snowflake.">
      {directory && <a className="snowflake-link" href={directory} target="_blank" rel="noopener noreferrer"><Icon name="snow" size={13}/>All Snowflake Agents<Icon name="external" size={11}/></a>}
    </SectionHeading>
    {executions.length ? <div className="managed-agent-list">{executions.map(execution => {
      const target = workspace?.agents?.[execution.role];
      const interrupted = ["FAILED", "CANCELLED", "SUPERSEDED"].includes(run.status) && execution.status !== "COMPLETED";
      return <article key={execution.thread_id} className="managed-agent-row">
        <div className="managed-agent-title"><Icon name="spark" size={18}/><strong>{words(execution.role)}</strong><Badge tone={execution.status === "COMPLETED" ? "green" : "blue"}>{interrupted ? "Interrupted" : words(execution.status)}</Badge></div>
        <p>{execution.agent_name}</p>
        {target && <div className="managed-agent-links"><a href={target.url} target="_blank" rel="noopener noreferrer">Agent page <Icon name="external" size={12}/></a><a href={target.executions_url} target="_blank" rel="noopener noreferrer">Execution history <Icon name="external" size={12}/></a></div>}
        <details><summary>Execution identifiers</summary><dl><dt>Thread ID</dt><dd>{String(execution.thread_id)}</dd><dt>Evidence snapshot</dt><dd>{execution.snapshot_id}</dd>{execution.turns.map((turn, index) => <div key={index}><dt>Request {index + 1}</dt><dd>{turn.request_id || turn.run_id || turn.assistant_message_id}</dd></div>)}</dl><p>In Snowflake’s Observability tab, search for this thread ID to inspect its recorded tool calls and responses. Logs can take time to appear.</p></details>
      </article>;
    })}</div> : <p className="managed-agent-note">{run.findings && Object.values(run.findings).some(f => f.provider === "Snowflake-hosted Claude") ? "This earlier assessment used hosted Claude calls and has no managed Agent execution. Reassess with the managed Agents configuration to create native execution records." : "Agent pages and execution identifiers appear here when the live investigation starts."}</p>}
  </section>;
}
