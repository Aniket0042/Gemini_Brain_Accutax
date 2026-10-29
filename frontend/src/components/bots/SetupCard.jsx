import React, { useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  Bot,
  Calendar,
  Check,
  Clock,
  Coins,
  HelpCircle,
  Plus,
  Play,
  Power,
  Shield,
  Trash2,
  X,
} from 'lucide-react';

const WATCH_ITEMS = [
  { id: 'duplicate_bill', label: 'A bill looks recorded twice', side: 'Purchases' },
  { id: 'vat_rate_wrong', label: 'VAT on a bill is not 0% or 5%', side: 'Purchases' },
  { id: 'bill_missing_details', label: 'Missing supplier, number, or future date', side: 'Purchases' },
  { id: 'unusual_bill', label: 'A bill far above the supplier’s usual amount', side: 'Purchases' },
  { id: 'sales_vat_wrong', label: 'A sales invoice with wrong or no output VAT', side: 'Sales' },
];

export default function SetupCard({
  draft,
  issues = [],
  nextRuns = [],
  credits = 0,
  onChange,
  onTry,
  onTurnOn,
  busy = false,
}) {
  const [newNote, setNewNote] = useState('');

  if (!draft) return null;

  const updateDraft = (patch) => {
    onChange({ ...draft, ...patch });
  };

  const hasBlockingIssues = issues.some((i) => !i.fixed);

  // Issues by field lookup
  const issueFor = (fieldName) =>
    issues.find((i) => i.field.startsWith(fieldName) && !i.fixed);

  // Watches toggle
  const toggleWatch = (id) => {
    const current = draft.watches || [];
    const updated = current.includes(id)
      ? current.filter((w) => w !== id)
      : [...current, id];
    updateDraft({ watches: updated });
  };

  // Delivery toggle
  const toggleDeliver = (id) => {
    if (id === 'inbox') return; // Inbox is required
    const current = draft.deliver || ['inbox'];
    const updated = current.includes(id)
      ? current.filter((d) => d !== id)
      : [...current, id];
    updateDraft({ deliver: updated });
  };

  // Schedule management
  const addSchedule = (kind = 'weekly') => {
    const defaultSched =
      kind === 'weekly'
        ? { kind: 'weekly', day: 'mon', time: '08:00' }
        : kind === 'monthly'
        ? { kind: 'monthly', day_of_month: 1, time: '08:00' }
        : kind === 'before_deadline'
        ? { kind: 'before_deadline', days_before: [14, 7, 1], time: '09:00' }
        : { kind: 'after_period_end', days_after: 3, time: '09:00' };

    updateDraft({ schedules: [...(draft.schedules || []), defaultSched] });
  };

  const updateSchedule = (index, patch) => {
    const updated = (draft.schedules || []).map((s, idx) =>
      idx === index ? { ...s, ...patch } : s
    );
    updateDraft({ schedules: updated });
  };

  const removeSchedule = (index) => {
    const updated = (draft.schedules || []).filter((_, idx) => idx !== index);
    updateDraft({ schedules: updated });
  };

  // Notes
  const handleAddNote = (e) => {
    e.preventDefault();
    if (!newNote.trim()) return;
    const notes = [...(draft.notes || []), newNote.trim()];
    updateDraft({ notes });
    setNewNote('');
  };

  const removeNote = (index) => {
    const notes = (draft.notes || []).filter((_, idx) => idx !== index);
    updateDraft({ notes });
  };

  return (
    <div className="setup-card">
      {/* ── Card Header ──────────────────────────────────────────────── */}
      <div className="setup-card-header">
        <div className="setup-avatar">
          <Bot className="w-6 h-6 text-accent" />
        </div>
        <div className="setup-title-box">
          <input
            type="text"
            className="setup-name-input"
            value={draft.name || ''}
            onChange={(e) => updateDraft({ name: e.target.value })}
            placeholder="Bot name (e.g. VAT Watch)"
            maxLength={60}
          />
          <div className="setup-meta-line">
            <span className="setup-agent-badge">VAT Agent</span>
            <span className="setup-cost-badge">
              <Coins className="w-3.5 h-3.5" />
              about {credits} credits / month
            </span>
          </div>
        </div>
      </div>

      {/* ── Questions (Clarifications) ─────────────────────────────────── */}
      {draft.questions && draft.questions.length > 0 && (
        <div className="setup-questions-box">
          <div className="setup-questions-header">
            <HelpCircle className="w-4 h-4 text-warning" />
            <span>Clarifications needed</span>
          </div>
          <ul className="setup-questions-list">
            {draft.questions.map((q, idx) => (
              <li key={idx} className="setup-question-item">
                <span>{q}</span>
                {q.toLowerCase().includes('often') && (
                  <div className="setup-quick-chips">
                    <button
                      type="button"
                      className="btn-chip"
                      onClick={() => addSchedule('weekly')}
                    >
                      Weekly (Mon)
                    </button>
                    <button
                      type="button"
                      className="btn-chip"
                      onClick={() => addSchedule('before_deadline')}
                    >
                      Before deadline
                    </button>
                    <button
                      type="button"
                      className="btn-chip"
                      onClick={() => addSchedule('monthly')}
                    >
                      Monthly
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Schedules ──────────────────────────────────────────────────── */}
      <div className="setup-section">
        <div className="setup-section-header">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-ink-soft" />
            <h4 className="setup-section-title">When it runs</h4>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="btn-text-small"
              onClick={() => addSchedule('weekly')}
            >
              + Weekly
            </button>
            <button
              type="button"
              className="btn-text-small"
              onClick={() => addSchedule('before_deadline')}
            >
              + Before deadline
            </button>
          </div>
        </div>

        {issueFor('schedules') && (
          <div className="setup-error-text">
            {issueFor('schedules').message}
          </div>
        )}

        <div className="setup-schedule-list">
          {(draft.schedules || []).map((s, idx) => (
            <div key={idx} className="setup-schedule-row">
              <select
                className="select-small"
                value={s.kind}
                onChange={(e) => updateSchedule(idx, { kind: e.target.value })}
              >
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="before_deadline">Before VAT deadline</option>
                <option value="after_period_end">After quarter ends</option>
              </select>

              {s.kind === 'weekly' && (
                <select
                  className="select-small"
                  value={s.day || 'mon'}
                  onChange={(e) => updateSchedule(idx, { day: e.target.value })}
                >
                  <option value="mon">Monday</option>
                  <option value="tue">Tuesday</option>
                  <option value="wed">Wednesday</option>
                  <option value="thu">Thursday</option>
                  <option value="fri">Friday</option>
                  <option value="sat">Saturday</option>
                  <option value="sun">Sunday</option>
                </select>
              )}

              {s.kind === 'monthly' && (
                <div className="flex items-center gap-1">
                  <span className="text-sm">Day</span>
                  <input
                    type="number"
                    className="input-num-small"
                    min={1}
                    max={28}
                    value={s.day_of_month || 1}
                    onChange={(e) =>
                      updateSchedule(idx, { day_of_month: parseInt(e.target.value, 10) || 1 })
                    }
                  />
                </div>
              )}

              {s.kind === 'before_deadline' && (
                <span className="text-sm text-ink-soft">
                  {(s.days_before || [14, 7, 1]).join(', ')} days before
                </span>
              )}

              {s.kind === 'after_period_end' && (
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    className="input-num-small"
                    min={0}
                    max={27}
                    value={s.days_after || 3}
                    onChange={(e) =>
                      updateSchedule(idx, { days_after: parseInt(e.target.value, 10) || 0 })
                    }
                  />
                  <span className="text-sm">days after</span>
                </div>
              )}

              <div className="flex items-center gap-1 ml-auto">
                <span className="text-xs text-ink-faint">at</span>
                <input
                  type="text"
                  className="input-time-small"
                  value={s.time || '08:00'}
                  onChange={(e) => updateSchedule(idx, { time: e.target.value })}
                  placeholder="08:00"
                />
                <button
                  type="button"
                  className="btn-icon-subtle"
                  onClick={() => removeSchedule(idx)}
                  title="Remove schedule"
                >
                  <Trash2 className="w-3.5 h-3.5 text-ink-faint" />
                </button>
              </div>
            </div>
          ))}

          {(!draft.schedules || draft.schedules.length === 0) && (
            <div className="setup-empty-hint">
              No schedule set yet. Click + Weekly or + Before deadline above.
            </div>
          )}
        </div>

        {nextRuns.length > 0 && (
          <div className="setup-next-runs">
            <span className="setup-next-label">Next runs:</span>
            <span className="setup-next-dates">
              {nextRuns.map((r) => r.reason.includes('due') ? `${r.reason}` : r.reason).join(' · ')}
            </span>
          </div>
        )}
      </div>

      {/* ── Watches ────────────────────────────────────────────────────── */}
      <div className="setup-section">
        <div className="setup-section-header">
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-ink-soft" />
            <h4 className="setup-section-title">What it watches</h4>
          </div>
          <div className="setup-sensitivity-toggle">
            <span className="text-xs text-ink-faint mr-1">Sensitivity:</span>
            {['fewer', 'balanced', 'more'].map((lvl) => (
              <button
                key={lvl}
                type="button"
                className={`btn-seg ${draft.sensitivity === lvl ? 'active' : ''}`}
                onClick={() => updateDraft({ sensitivity: lvl })}
              >
                {lvl}
              </button>
            ))}
          </div>
        </div>

        <div className="setup-watches-list">
          {WATCH_ITEMS.map((w) => {
            const isChecked = (draft.watches || []).includes(w.id);
            return (
              <label key={w.id} className="setup-watch-row">
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggleWatch(w.id)}
                />
                <span className="setup-watch-label">{w.label}</span>
                <span className="setup-watch-side">{w.side}</span>
              </label>
            );
          })}
        </div>
      </div>

      {/* ── Delivery ───────────────────────────────────────────────────── */}
      <div className="setup-section">
        <h4 className="setup-section-title">Deliver findings to</h4>
        <div className="setup-deliver-chips">
          <button
            type="button"
            className="deliver-chip active"
            disabled
            title="Inbox delivery is always enabled"
          >
            <Check className="w-3.5 h-3.5" />
            Inbox
          </button>

          <button
            type="button"
            className="deliver-chip disabled"
            disabled
            title="Email delivery pending email service configuration"
          >
            Email digest <span className="chip-badge">soon</span>
          </button>

          <button
            type="button"
            className={`deliver-chip ${(draft.deliver || []).includes('bell') ? 'active' : ''}`}
            onClick={() => toggleDeliver('bell')}
          >
            {(draft.deliver || []).includes('bell') && <Check className="w-3.5 h-3.5" />}
            Bell notifications
          </button>

          <button type="button" className="deliver-chip disabled" disabled>
            WhatsApp <span className="chip-badge">soon</span>
          </button>
        </div>
      </div>

      {/* ── Rules ──────────────────────────────────────────────────────── */}
      <div className="setup-section">
        <h4 className="setup-section-title">Rules & Safety</h4>
        <div className="setup-rules-grid">
          <div className="setup-rule-row">
            <span>Scan and review bills</span>
            <span className="rule-val-badge">Allowed</span>
          </div>
          <div className="setup-rule-row">
            <span>Export audit findings</span>
            <span className="rule-val-badge">Allowed</span>
          </div>
          <div className="setup-rule-row">
            <span>Email outside organization</span>
            <span className="rule-val-badge warning">Ask first</span>
          </div>
          <div className="setup-rule-row immutable">
            <span>Change bills or file the VAT return</span>
            <span className="rule-val-badge danger">Never (read-only)</span>
          </div>
        </div>
      </div>

      {/* ── Notes ──────────────────────────────────────────────────────── */}
      <div className="setup-section">
        <h4 className="setup-section-title">Instructions & Notes</h4>
        <div className="setup-notes-list">
          {(draft.notes || []).map((n, idx) => (
            <div key={idx} className="setup-note-item">
              <span>{n}</span>
              <button
                type="button"
                className="btn-icon-subtle"
                onClick={() => removeNote(idx)}
              >
                <X className="w-3.5 h-3.5 text-ink-faint" />
              </button>
            </div>
          ))}
        </div>
        <form onSubmit={handleAddNote} className="setup-add-note-form">
          <input
            type="text"
            className="input-text-clean"
            placeholder="Add note for this bot (e.g. Ignore bills under 100 AED)..."
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            maxLength={200}
          />
          <button type="submit" className="btn-secondary flex items-center gap-1">
            <Plus className="w-3.5 h-3.5" />
            Add
          </button>
        </form>
      </div>

      {/* ── Issues Banner ──────────────────────────────────────────────── */}
      {hasBlockingIssues && (
        <div className="setup-issues-banner">
          <AlertCircle className="w-4 h-4 text-danger flex-shrink-0" />
          <div>
            <strong>Please resolve the following:</strong>
            <ul className="list-disc pl-4 mt-1 text-sm">
              {issues
                .filter((i) => !i.fixed)
                .map((iss, idx) => (
                  <li key={idx}>{iss.message}</li>
                ))}
            </ul>
          </div>
        </div>
      )}

      {/* ── Action Buttons ─────────────────────────────────────────────── */}
      <div className="setup-actions">
        <button
          type="button"
          className="btn-secondary flex items-center gap-2"
          onClick={onTry}
          disabled={busy}
        >
          <Play className="w-4 h-4" />
          <span>Try it on last month</span>
        </button>

        <button
          type="button"
          className="btn-primary flex items-center gap-2"
          onClick={onTurnOn}
          disabled={busy || hasBlockingIssues}
          title={hasBlockingIssues ? 'Fix blocking issues above to turn on' : 'Turn on bot'}
        >
          <Power className="w-4 h-4" />
          <span>Turn on</span>
        </button>
      </div>
    </div>
  );
}
