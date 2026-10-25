import React, { useEffect, useState } from 'react';
import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react';
import { AgentIcon } from './AgentsPanel';

/**
 * The live run card of a specialist agent: its plan, each step ticking off as the tool call finishes
 * (agent_step events on /query/stream), then "Writing the answer". Steps it did not need stay hidden.
 */

/** A new run for the assistant turn of `agent` (from GET /agents). */
export function newAgentRun(agent, scopeLabel) {
  return { key: agent.key, name: agent.name, icon: agent.icon, plan: agent.plan || [], scopeLabel,
    steps: [], startedAt: Date.now(), finishedAt: null };
}

/** The run with one agent_step event applied: {phase: start|done, tool, label?, ok?, ms?}. */
export function applyAgentStep(run, event) {
  if (!run || !event?.tool) return run;
  const steps = [...run.steps];
  if (event.phase === 'start') {
    steps.push({ tool: event.tool, label: event.label, state: 'running' });
  } else if (event.phase === 'done') {
    const i = steps.findIndex((s) => s.tool === event.tool && s.state === 'running');
    const done = { state: event.ok === false ? 'failed' : 'done', ms: event.ms };
    if (i >= 0) steps[i] = { ...steps[i], ...done };
    else steps.push({ tool: event.tool, label: event.label, ...done });
  }
  return { ...run, steps };
}

/** The rows the card shows: the plan's steps in order (waiting, running or done), any other tools it called, then the answer. */
function rows(run, streaming) {
  const used = new Set();
  // The model reads the question and picks its tools before the first call starts.
  const out = [{ label: 'Plan the work', state: run.steps.length || !streaming ? 'done' : 'running' }];
  for (const p of run.plan) {
    const calls = run.steps.filter((s) => s.tool === p.tool);
    if (!calls.length) {
      if (p.expected && streaming) out.push({ label: p.label, state: 'waiting' });
      continue;
    }
    calls.forEach((c) => used.add(c));
    const running = calls.some((c) => c.state === 'running');
    const failed = !running && calls.every((c) => c.state === 'failed');
    out.push({ label: p.label + (calls.length > 1 ? ` (${calls.length})` : ''),
      state: running ? 'running' : failed ? 'failed' : 'done',
      ms: running ? null : Math.max(...calls.map((c) => c.ms || 0)) });
  }
  run.steps.filter((s) => !used.has(s)).forEach((s) => out.push({ label: s.label || s.tool, state: s.state, ms: s.ms }));
  const busy = out.some((r) => r.state === 'running');
  const anyDone = out.some((r) => r.state === 'done' || r.state === 'failed');
  if (streaming) {
    out.push({ label: 'Writing the answer', state: !busy && anyDone ? 'running' : 'waiting' });
  } else {
    out.push({ label: 'Wrote the answer', state: 'done' });
  }
  return out;
}

const VERB = { vat: 'reviewing', ap: 'checking bills for', cash: 'projecting cash for', reporting: 'building the pack for' };

function seconds(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.max(0, Math.round(ms || 0))}ms`;
}

function StepIcon({ state }) {
  if (state === 'done') return <CheckCircle2 size={15} className="run-ok" />;
  if (state === 'failed') return <XCircle size={15} className="run-fail" />;
  if (state === 'running') return <Loader2 size={15} className="run-spin" />;
  return <Circle size={15} className="run-wait" />;
}

export function AgentRunCard({ run, streaming, elapsedSeconds }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!streaming) return undefined;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [streaming]);
  if (!run) return null;
  const took = streaming ? (now - run.startedAt) / 1000 : (elapsedSeconds ?? ((run.finishedAt || now) - run.startedAt) / 1000);
  const list = rows(run, streaming);
  const scope = run.scopeLabel ? ` ${run.scopeLabel}` : '';
  return (
    <div className={`agent-run-card${streaming ? ' is-live' : ''}`}>
      <div className="agent-run-head">
        <span className={`agent-badge-icon agent-${run.key}`}><AgentIcon icon={run.icon} size={12} /></span>
        <span className="agent-run-title">
          {streaming ? `${run.name} is ${VERB[run.key] || 'working on'}${scope}` : `${run.name} finished`}
        </span>
        <span className="agent-run-time">{Number.isFinite(took) ? `${took.toFixed(0)}s` : ''}</span>
      </div>
      <ol className="agent-run-steps">
        {list.map((r, i) => (
          <li key={i} className={`agent-run-step is-${r.state}`}>
            <StepIcon state={r.state} />
            <span className="agent-run-label">{r.label}</span>
            <span className="agent-run-meta">
              {(r.state === 'done' || r.state === 'failed') && r.ms ? seconds(r.ms) : ''}
              {r.state === 'failed' ? ' · could not finish' : ''}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
