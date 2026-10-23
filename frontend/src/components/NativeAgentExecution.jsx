export default function NativeAgentExecution({ finding, cloud }) {
  const coordinator = finding.execution;
  const producer = finding.agent_execution || coordinator;
  const link = execution => Object.values(cloud.agents || {}).find(a => a.object === execution.agent)?.url || cloud.agent_url;
  return <>
    <p>Producing agent: {producer.agent}</p>
    <p>Thread: {producer.thread_id}</p>
    <p>Runs: {producer.turns.map(t => t.run_id || t.request_id || t.assistant_message_id).join(', ')}</p>
    <p>Tools: {producer.tools.map(t => t.name).join(', ')}</p>
    <a href={link(producer)} target="_blank" rel="noreferrer">Open producing Cortex Agent in Snowflake</a>
    <p>Coordinator: {coordinator.agent} · Thread {coordinator.thread_id}</p>
    <a href={link(coordinator)} target="_blank" rel="noreferrer">Open coordinator in Snowflake</a>
    {(coordinator.children || []).map(child => <div key={child.agent}>
      <p>{child.kind === 'risk_detection' ? 'Risk Detection' : 'Recovery Plan'} · Thread {child.thread_id}</p>
      <a href={link(child)} target="_blank" rel="noreferrer">Open specialist in Snowflake</a>
    </div>)}
  </>;
}
