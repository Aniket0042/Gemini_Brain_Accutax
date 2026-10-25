import React from 'react';
import { Sparkles, Receipt, FileCheck, Wallet, FileBarChart, Play, MessageSquare, Clock } from 'lucide-react';

/**
 * Specialist agents (GET /api/v1/agents): the cards on the start screen, the picker above the composer,
 * and the badge over an answer. Each agent is the same Accutax AI with fewer tools and its own job.
 */

const ICONS = { sparkles: Sparkles, receipt: Receipt, 'file-check': FileCheck, wallet: Wallet, 'file-chart': FileBarChart };

export function AgentIcon({ icon, size = 15 }) {
  const Icon = ICONS[icon] || Sparkles;
  return <Icon size={size} strokeWidth={2} />;
}

/**
 * "@vat check my bills" -> { agentKey: 'vat', text: 'check my bills' }. The mention is the agent's key or the
 * first word of its name, any case. Text without a known mention comes back as it is, with agentKey null.
 */
export function parseMention(text, agents) {
  const m = /^\s*@(\S+)\s*/.exec(text || '');
  if (!m || !Array.isArray(agents)) return { agentKey: null, text };
  const word = m[1].toLowerCase().replace(/[^a-z]/g, '');
  const agent = agents.find((a) => a.key === word || a.name.split(/\s+/)[0].toLowerCase().replace(/[^a-z]/g, '') === word);
  if (!agent) return { agentKey: null, text };
  return { agentKey: agent.key, text: text.slice(m[0].length) };
}

/** The specialist cards on the start screen: what each does, Run now, and Chat with it. */
export function AgentsPanel({ agents, activeKey, onRunNow, onSelect, disabled }) {
  const specialists = (agents || []).filter((a) => !a.default);
  if (!specialists.length) return null;
  return (
    <section className="agents-panel" aria-label="Agents">
      <div className="agents-panel-head">
        <span className="agents-panel-title">Your agents</span>
        <span className="agents-panel-sub">Each one works on its own job with its own tools. Run one now, or chat with it.</span>
      </div>
      <div className="agents-grid">
        {specialists.map((agent) => (
          <article key={agent.key} className={`agent-card${activeKey === agent.key ? ' is-active' : ''}`}>
            <div className="agent-card-top">
              <div className={`agent-card-icon agent-${agent.key}`}>
                <AgentIcon icon={agent.icon} size={16} />
              </div>
              <div className="agent-card-name">{agent.name}</div>
            </div>
            <p className="agent-card-role">{agent.role}</p>
            <div className="agent-card-actions">
              <button type="button" className="agent-run-btn" disabled={disabled}
                onClick={() => onRunNow(agent)} title={agent.run_prompt}>
                <Play size={12} strokeWidth={2.4} />
                <span>Run now</span>
              </button>
              <button type="button" className="agent-chat-btn" onClick={() => onSelect(agent.key)}
                aria-pressed={activeKey === agent.key}>
                <MessageSquare size={12} strokeWidth={2.2} />
                <span>{activeKey === agent.key ? 'Chatting' : 'Chat'}</span>
              </button>
            </div>
            <div className="agent-card-run-label">{agent.run_label}</div>
            {agent.routines?.length > 0 && (
              <div className="agent-card-routines" title="Runs on its own once scheduling is switched on">
                <Clock size={11} />
                <span>Soon on its own: {agent.routines.join(' · ')}</span>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

/** The row above the composer: which agent answers the next question, and Run now for a specialist. */
export function AgentBar({ agents, activeKey, onSelect, onRunNow, disabled }) {
  if (!Array.isArray(agents) || agents.length < 2) return null;
  const active = agents.find((a) => a.key === activeKey) || agents.find((a) => a.default);
  return (
    <div className="agent-bar" role="toolbar" aria-label="Choose an agent">
      <span className="agent-bar-label">Ask</span>
      {agents.map((agent) => (
        <button key={agent.key} type="button"
          className={`agent-chip${active?.key === agent.key ? ' is-active' : ''}`}
          aria-pressed={active?.key === agent.key}
          onClick={() => onSelect(agent.key)}>
          <AgentIcon icon={agent.icon} size={12} />
          <span>{agent.name}</span>
        </button>
      ))}
      {active && !active.default && (
        <button type="button" className="agent-chip agent-chip-run" disabled={disabled}
          onClick={() => onRunNow(active)} title={active.run_prompt}>
          <Play size={11} strokeWidth={2.4} />
          <span>Run now</span>
        </button>
      )}
      <span className="agent-bar-hint">or type @vat, @ap, @cash, @reporting</span>
    </div>
  );
}

/** The line over a specialist's answer that names it (the agent_badge block, saved with the turn). */
export function AgentBadge({ block }) {
  if (!block) return null;
  return (
    <div className="agent-badge">
      <span className={`agent-badge-icon agent-${block.key}`}><AgentIcon icon={block.icon} size={11} /></span>
      <span>{block.name}</span>
    </div>
  );
}
