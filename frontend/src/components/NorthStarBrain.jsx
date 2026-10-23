import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { Badge, Icon } from './ui';

export default function NorthStarBrain({ data, onSource, onCompare, expanded, onToggle }) {
  const [messages, setMessages] = useState([]), [question, setQuestion] = useState(''), [asking, setAsking] = useState(false);
  const end = useRef(null);
  useEffect(() => { setMessages([]); }, [data.snapshot_id]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [messages, asking]);
  async function ask(text) {
    if (!text.trim() || asking) return;
    setQuestion(''); setAsking(true);
    setMessages(prev => [...prev, { role: 'user', text }]);
    try {
      const answer = await api('/northstar/chat', { question: text, revision: data.revision, snapshot_id: data.snapshot_id });
      setMessages(prev => [...prev, { role: 'assistant', ...answer }]);
    } catch (e) { setMessages(prev => [...prev, { role: 'assistant', text: e.message, error: true }]); }
    finally { setAsking(false); }
  }
  return <aside className={`company-brain ${expanded ? 'expanded' : ''}`} aria-label="Company Brain assistant"><div className="brain-heading"><span className="brain-logo"><Icon name="brain" size={23}/></span><div><h2>Company Brain</h2><span><i/>Supply chain copilot</span></div><button className="icon-button brain-toggle" onClick={onToggle} aria-label={expanded ? 'Minimize assistant' : 'Open assistant'}><Icon name={expanded ? 'close' : 'chevron'} size={17}/></button></div><div className="brain-context"><Icon name="snow" size={14}/><span>Snowflake Cortex · Claude</span><Badge tone={data.mode === 'live' ? 'green' : 'neutral'}>{data.mode === 'live' ? 'Live' : 'Unavailable'}</Badge></div><div className="brain-messages" aria-live="polite" aria-busy={asking}>{!messages.length && <><div className="brain-welcome"><span className="ai-avatar"><Icon name="spark" size={19}/></span><h3>Let’s find a way forward.</h3><p>Ask about the risk, the evidence, or the trade-offs.</p></div><div className="suggested-label">EXPLORE THIS CASE</div><div className="suggestions">{['Which customer deliveries are at risk?', 'Why recommend the hybrid recovery?', 'What is still uncovered?'].map(q => <button key={q} disabled={asking} onClick={() => ask(q)}>{q}<Icon name="arrow" size={14}/></button>)}</div><button className="nr-brain-compare" onClick={onCompare}><Icon name="compare" size={18}/><span>Compare recovery options</span><Icon name="arrow" size={14}/></button></>}{messages.map((m, i) => <div className={`chat-message ${m.role}`} key={i}><span className={m.role === 'user' ? 'user-avatar' : 'ai-avatar'}>{m.role === 'user' ? 'SC' : <Icon name="spark" size={14}/>}</span><div className={`message-body ${m.error ? 'message-error' : ''}`}><small>{m.role === 'user' ? 'You' : 'Company Brain'}</small><p>{m.text}</p>{m.citations?.length > 0 && <div className="chat-sources">{m.citations.map(id => <button key={id} onClick={() => onSource(id)}><Icon name="file" size={11}/>{id}</button>)}</div>}{m.query_id && <details className="nr-technical"><summary>Source & execution</summary><p>{m.model} · Snowflake AI_COMPLETE</p><p>Query {m.query_id}</p></details>}</div></div>)}{asking && <div className="thinking" role="status"><span className="spinner"/>Reviewing Snowflake evidence…</div>}<div ref={end}/></div><div className="brain-compose"><form onSubmit={e => { e.preventDefault(); ask(question); }}><textarea aria-label="Ask Company Brain" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask Company Brain…" rows={2} maxLength={2000} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(question); } }}/><button className="send-button" aria-label="Send question" disabled={asking || !question.trim()}><Icon name="send" size={17}/></button></form><small>Grounded in this case’s dated evidence.</small></div></aside>;
}
