import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { Badge, Icon } from './ui';
import NativeAgentExecution from './NativeAgentExecution';

function savedConversation(key) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key));
    if (Array.isArray(saved?.messages)) return saved;
  } catch { /* Storage can be unavailable in private browser sessions. */ }
  return { messages: [], job: null };
}

export default function NorthStarBrain(props) {
  const storageKey = `northstar-brain:${props.data.run_id}:${props.data.snapshot_id}`;
  return <BrainSession key={storageKey} storageKey={storageKey} {...props}/>;
}

function BrainSession({ data, onSource, onCompare, expanded, onToggle, storageKey }) {
  const [conversation, setConversation] = useState(() => savedConversation(storageKey));
  const [question, setQuestion] = useState(''), [submitting, setSubmitting] = useState(false), [now, setNow] = useState(Date.now());
  const { messages, job } = conversation;
  const asking = submitting || !!job;
  const submitAbort = useRef(null), submittingRef = useRef(false);
  const end = useRef(null);
  useEffect(() => { try { sessionStorage.setItem(storageKey, JSON.stringify(conversation)); } catch { /* Keep chat usable without storage. */ } }, [storageKey, conversation]);
  useEffect(() => () => submitAbort.current?.abort(), []);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [messages, asking]);
  useEffect(() => {
    if (!job) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [job?.id]);
  function finish(result) {
    const reply = result.status === 'completed'
      ? { role: 'assistant', ...result.result, response_id: result.id }
      : { role: 'assistant', text: result.error, error: true, question: result.question };
    setConversation(previous => ({ messages: [...previous.messages.filter(message =>
      message.job_id !== result.id && message.response_id !== result.id &&
      !(reply.execution?.thread_id && message.execution?.thread_id === reply.execution.thread_id) &&
      !(reply.query_id && message.query_id === reply.query_id)), reply].slice(-40), job: null }));
  }
  useEffect(() => {
    if (!job) return;
    let active = true, timer, failures = 0;
    const controller = new AbortController();
    async function poll() {
      try {
        const next = await api(`/northstar/chat/jobs/${job.id}`, undefined, {}, { signal: controller.signal, timeoutMs: 15000 });
        if (!active) return;
        failures = 0;
        if (['completed', 'failed'].includes(next.status)) { finish(next); return; }
        setConversation(previous => previous.job?.id === job.id ? { ...previous, job: next } : previous);
      } catch (e) {
        if (!active) return;
        failures += 1;
        if (failures >= 3 || Date.now() - job.created_at * 1000 > 390000) {
          setConversation(previous => ({ messages: [...previous.messages, { role: 'assistant', error: true, text: 'We couldn’t check the review’s status. Check the response again to reconnect without starting another review.', job_id: job.id }], job: null }));
          return;
        }
        setConversation(previous => previous.job?.id === job.id ? { ...previous, job: { ...previous.job, progress: 'Reconnecting to the evidence review…' } } : previous);
      }
      if (active) timer = setTimeout(poll, 1500);
    }
    poll();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [job?.id]);
  async function ask(text) {
    if (!text.trim() || asking || submittingRef.current) return;
    submittingRef.current = true;
    setQuestion(''); setSubmitting(true);
    setConversation(previous => ({ ...previous, messages: [...previous.messages, { role: 'user', text }].slice(-40) }));
    const controller = new AbortController();
    submitAbort.current = controller;
    try {
      const next = await api('/northstar/chat/jobs', { question: text, revision: data.revision, snapshot_id: data.snapshot_id }, {}, { signal: controller.signal, timeoutMs: 30000 });
      if (['completed', 'failed'].includes(next.status)) finish(next);
      else setConversation(previous => ({ ...previous, job: next }));
    } catch (e) {
      if (!controller.signal.aborted) setConversation(previous => ({ ...previous, messages: [...previous.messages, { role: 'assistant', text: e.message, error: true, question: text }] }));
    } finally { submittingRef.current = false; setSubmitting(false); }
  }
  function stopWaiting() {
    setConversation(previous => previous.job ? { messages: [...previous.messages, { role: 'assistant', error: true, text: 'The review continues in the background. Check the response whenever you’re ready.', job_id: previous.job.id }], job: null } : previous);
  }
  function checkResponse(id) {
    if (!asking) setConversation(previous => ({ messages: previous.messages.filter(message => message.job_id !== id), job: { id, created_at: Date.now() / 1000, progress: 'Checking your saved response…' } }));
  }
  const elapsed = job ? Math.max(job.elapsed_seconds || 0, Math.floor((now - job.created_at * 1000) / 1000), 0) : 0;
  return <aside className={`company-brain ${expanded ? 'expanded' : ''}`} aria-label="Company Brain assistant"><div className="brain-heading"><span className="brain-logo"><Icon name="brain" size={23}/></span><div><h2>Company Brain</h2><span><i/>Supply chain copilot</span></div><button className="icon-button brain-toggle" onClick={onToggle} aria-label={expanded ? 'Minimize assistant' : 'Open assistant'}><Icon name={expanded ? 'close' : 'chevron'} size={17}/></button></div><div className="brain-context"><Icon name="snow" size={14}/><span>Snowflake Cortex · Claude</span><Badge tone={data.mode === 'live' ? 'green' : 'neutral'}>{data.mode === 'live' ? 'Live' : 'Unavailable'}</Badge></div><div className="brain-messages" aria-live="polite" aria-busy={asking}>{!messages.length && <><div className="brain-welcome"><span className="ai-avatar"><Icon name="spark" size={19}/></span><h3>Let’s find a way forward.</h3><p>Ask about the risk, the evidence, or the trade-offs.</p></div><div className="suggested-label">EXPLORE THIS CASE</div><div className="suggestions">{['Which customer deliveries are at risk?', 'Why recommend the hybrid recovery?', 'What is still uncovered?'].map(q => <button key={q} disabled={asking} onClick={() => ask(q)}>{q}<Icon name="arrow" size={14}/></button>)}</div><button className="nr-brain-compare" onClick={onCompare}><Icon name="compare" size={18}/><span>Compare recovery options</span><Icon name="arrow" size={14}/></button></>}{messages.map((m, i) => <div className={`chat-message ${m.role}`} key={i}><span className={m.role === 'user' ? 'user-avatar' : 'ai-avatar'}>{m.role === 'user' ? 'SC' : <Icon name="spark" size={14}/>}</span><div className={`message-body ${m.error ? 'message-error' : ''}`}><small>{m.role === 'user' ? 'You' : 'Company Brain'}</small><p>{m.text}</p>{m.error && (m.job_id || m.question) && <button className="text-button nr-chat-retry" disabled={asking} onClick={() => m.job_id ? checkResponse(m.job_id) : ask(m.question)}>{m.job_id ? 'Check response' : 'Try again'}</button>}{m.citations?.length > 0 && <div className="chat-sources">{m.citations.map(id => <button key={id} onClick={() => onSource(id)}><Icon name="file" size={11}/>{id}</button>)}</div>}{(m.query_id || m.execution) && <details className="nr-technical"><summary>Source & execution</summary><p>{m.model} · {m.execution ? 'Snowflake Cortex Agents' : 'Snowflake AI_COMPLETE'}</p>{m.execution ? <NativeAgentExecution finding={m} cloud={data.cloud}/> : <p>Query {m.query_id}</p>}</details>}</div></div>)}{asking && <div className="nr-chat-progress" role="status"><div className="thinking"><span className="spinner"/>{job?.progress || 'Starting the Snowflake evidence review…'}</div>{job && <><small>{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')} elapsed · You can switch pages while this runs.</small><button className="text-button" onClick={stopWaiting}>Stop waiting</button></>}</div>}<div ref={end}/></div><div className="brain-compose"><form onSubmit={e => { e.preventDefault(); ask(question); }}><textarea aria-label="Ask Company Brain" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask Company Brain…" rows={2} maxLength={2000} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(question); } }}/><button className="send-button" aria-label="Send question" disabled={asking || !question.trim()}><Icon name="send" size={17}/></button></form><small>Grounded in this case’s dated evidence.</small></div></aside>;
}
