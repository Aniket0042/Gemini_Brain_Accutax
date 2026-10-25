import React from 'react';
import { Sparkles, Receipt, FileCheck, Wallet, FileBarChart, Play, MessageSquare, Clock, Bell, ArrowUpRight } from 'lucide-react';
import { timeAgo } from '../services/agentRuns';

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

/** The coloured dot and the status line of one agent card: working, its last run, or not run yet. */
function AgentStatus({ agent, run, running, onOpenRun }) {
  if (running) {
    return (
      <div className="agent-status">
        <div className="agent-status-line"><span className="agent-dot tone-working" />Working…</div>
        <div className="agent-progress"><span /></div>
      </div>
    );
  }
  if (!run?.status) {
    return (
      <div className="agent-status">
        <div className="agent-status-line is-idle"><span className="agent-dot tone-idle" />Not run yet · {agent.run_label}</div>
      </div>
    );
  }
  return (
    <div className="agent-status">
      <div className="agent-status-line"><span className={`agent-dot tone-${run.status.tone}`} />
        {run.status.headline} · {timeAgo(run.at)}</div>
      <div className="agent-status-row">
        <span className={`agent-pill tone-${run.status.tone}`}>{run.status.badge}</span>
        {run.historyId && <button type="button" className="agent-open-btn" onClick={() => onOpenRun(run.historyId)}>
          Open result<ArrowUpRight size={11} /></button>}
      </div>
    </div>
  );
}

/** "Latest from your agents": each agent's last result for this organization, newest first. */
function AgentBriefing({ agents, runs, onOpenRun }) {
  const items = agents.filter((a) => runs[a.key]?.status).sort((a, b) => runs[b.key].at - runs[a.key].at);
  if (!items.length) return null;
  return (
    <div className="agent-briefing">
      <div className="agent-briefing-title"><Bell size={12} />Latest from your agents</div>
      <ul>
        {items.map((a) => {
          const r = runs[a.key];
          return (
            <li key={a.key}>
              <span className={`agent-badge-icon agent-${a.key}`}><AgentIcon icon={a.icon} size={11} /></span>
              <span className="agent-briefing-name">{a.name}</span>
              <span className={`agent-pill tone-${r.status.tone}`}>{r.status.badge}</span>
              <span className="agent-briefing-time">{timeAgo(r.at)}</span>
              {r.historyId && <button type="button" className="agent-open-btn" onClick={() => onOpenRun(r.historyId)}>
                Open<ArrowUpRight size={11} /></button>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The agent team on the start screen: a briefing of what each agent last found, then a card per agent with
 * its status (working, last run, not run yet), Run now and Chat. `runs`: services/agentRuns for this scope.
 */
export function AgentsPanel({ agents, activeKey, onRunNow, onSelect, disabled, runs = {}, runningKey, onOpenRun }) {
  const specialists = (agents || []).filter((a) => !a.default);
  if (!specialists.length) return null;
  return (
    <section className="agents-panel" aria-label="Agents">
      <div className="agents-panel-head">
        <span className="agents-panel-title">Your agents</span>
        <span className="agents-panel-sub">Each one works on its own job with its own tools. Run one now, or chat with it.</span>
      </div>
      <AgentBriefing agents={specialists} runs={runs} onOpenRun={onOpenRun} />
      <div className="agents-grid">
        {specialists.map((agent) => (
          <article key={agent.key} className={`agent-card${activeKey === agent.key ? ' is-active' : ''}${runningKey === agent.key ? ' is-running' : ''}`}>
            <div className="agent-card-top">
              <div className={`agent-card-icon agent-${agent.key}`}>
                <AgentIcon icon={agent.icon} size={16} />
              </div>
              <div className="agent-card-name">{agent.name}</div>
            </div>
            <p className="agent-card-role">{agent.role}</p>
            <AgentStatus agent={agent} run={runs[agent.key]} running={runningKey === agent.key} onOpenRun={onOpenRun} />
            <div className="agent-card-actions">
              <button type="button" className="agent-run-btn" disabled={disabled}
                onClick={() => onRunNow(agent)} title={agent.run_prompt}>
                <Play size={12} strokeWidth={2.4} />
                <span>{runs[agent.key] ? 'Run again' : 'Run now'}</span>
              </button>
              <button type="button" className="agent-chat-btn" onClick={() => onSelect(agent.key)}
                aria-pressed={activeKey === agent.key}>
                <MessageSquare size={12} strokeWidth={2.2} />
                <span>{activeKey === agent.key ? 'Chatting' : 'Chat'}</span>
              </button>
            </div>
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
