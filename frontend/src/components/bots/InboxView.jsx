import React, { useState } from 'react';
import {
  AlertTriangle,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Edit3,
  ExternalLink,
  Eye,
  HelpCircle,
  Mail,
  RefreshCw,
  ShieldAlert,
  Sliders,
  Sparkles,
  X,
} from 'lucide-react';

export default function InboxView({
  inbox = { unread_count: 0, bots_count: 0, runs: [], findings: [], approvals: [] },
  onDismissFinding,
  onUndismissFinding,
  onMarkSeen,
  onRefresh,
  onSeeFindingsInChat,
  onEditBot,
  onApproveAction,
  onRejectAction,
  onEditApproveAction,
  loading = false,
}) {
  const [filterState, setFilterState] = useState('all'); // 'all' | 'new' | 'seen'
  const [dismissingKey, setDismissingKey] = useState(null);
  const [expandedWhy, setExpandedWhy] = useState({});
  const [editingApprovalId, setEditingApprovalId] = useState(null);
  const [editForm, setEditForm] = useState({ recipient: '', content: '' });

  const toggleWhy = (key) => {
    setExpandedWhy((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const { unread_count = 0, runs = [], findings = [], approvals = [] } = inbox;

  const filteredFindings = findings.filter((f) => {
    if (filterState === 'new') return f.state === 'new';
    if (filterState === 'seen') return f.state === 'seen';
    return true;
  });

  const handleDismiss = async (botId, key, supplier) => {
    setDismissingKey(key);
    try {
      await onDismissFinding?.(botId, key, '', supplier);
    } finally {
      setDismissingKey(null);
    }
  };

  return (
    <div className="bot-inbox-view">
      <div className="bot-inbox-header">
        <div className="flex items-center gap-3">
          <h3 className="bot-list-title" style={{ margin: 0 }}>Bot Inbox</h3>
          {unread_count > 0 && (
            <span className="bot-unread-badge">
              {unread_count} new
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {unread_count > 0 && onMarkSeen && (
            <button
              type="button"
              className="btn-secondary flex items-center gap-1.5 text-xs py-1 px-2.5"
              onClick={onMarkSeen}
              title="Mark all new items as seen"
            >
              <Check className="w-3.5 h-3.5" />
              <span>Mark all read</span>
            </button>
          )}
          {onRefresh && (
            <button
              type="button"
              className="btn-icon-subtle"
              onClick={onRefresh}
              disabled={loading}
              title="Refresh inbox"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          )}
        </div>
      </div>

      {/* Pending Approvals Section (Phase 4) */}
      {approvals.length > 0 && (
        <div className="bot-inbox-approvals mb-4">
          <div className="bot-section-title flex items-center gap-1.5" style={{ color: '#d97706', fontWeight: 600 }}>
            <ShieldAlert className="w-4 h-4" />
            <span>Action Approvals Required ({approvals.length})</span>
          </div>
          <div className="bot-approvals-list">
            {approvals.map((appr) => {
              const isEditing = editingApprovalId === appr.id;
              return (
                <div key={appr.id} className="bot-approval-card">
                  <div className="bot-approval-header">
                    <div className="flex items-center gap-2">
                      <Mail className="w-4 h-4 text-warning" />
                      <span className="bot-approval-title">{appr.title || 'Send Email Outside'}</span>
                      <span className="bot-meta-tag warning">Needs Approval</span>
                    </div>
                    <span className="bot-run-botname">{appr.bot_name || 'VAT Watch'}</span>
                  </div>

                  {isEditing ? (
                    <div className="bot-approval-edit-form my-2">
                      <label className="text-xs text-ink-faint block mb-1">Recipient Email:</label>
                      <input
                        type="email"
                        className="bot-input-field mb-2 w-full text-xs"
                        style={{ padding: '6px 8px', borderRadius: '6px', border: '1px solid var(--border)' }}
                        value={editForm.recipient}
                        onChange={(e) => setEditForm((prev) => ({ ...prev, recipient: e.target.value }))}
                      />
                      <label className="text-xs text-ink-faint block mb-1">Message Content:</label>
                      <textarea
                        className="bot-input-field text-xs mb-2 w-full"
                        style={{ padding: '6px 8px', borderRadius: '6px', border: '1px solid var(--border)' }}
                        rows={2}
                        value={editForm.content}
                        onChange={(e) => setEditForm((prev) => ({ ...prev, content: e.target.value }))}
                      />
                      <div className="flex items-center gap-2 mt-1">
                        <button
                          type="button"
                          className="btn-primary text-xs py-1 px-3"
                          onClick={() => {
                            onEditApproveAction?.(appr.bot_id, appr.id, editForm);
                            setEditingApprovalId(null);
                          }}
                        >
                          Save & Approve
                        </button>
                        <button
                          type="button"
                          className="btn-secondary text-xs py-1 px-2.5"
                          onClick={() => setEditingApprovalId(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="bot-approval-details my-2">
                        <div className="text-xs text-ink-muted">
                          <strong>Recipient:</strong> {appr.recipient}
                        </div>
                        <div className="text-xs text-ink-muted mt-1">
                          <strong>Summary:</strong> {appr.content || appr.subject}
                        </div>
                      </div>

                      <div className="bot-approval-actions">
                        <button
                          type="button"
                          className="btn-primary text-xs py-1 px-3 flex items-center gap-1.5"
                          onClick={() => onApproveAction?.(appr.bot_id, appr.id)}
                        >
                          <Check className="w-3.5 h-3.5" />
                          <span>Approve</span>
                        </button>
                        <button
                          type="button"
                          className="btn-secondary text-xs py-1 px-2.5 flex items-center gap-1.5"
                          onClick={() => {
                            setEditingApprovalId(appr.id);
                            setEditForm({ recipient: appr.recipient || '', content: appr.content || '' });
                          }}
                        >
                          <Edit3 className="w-3.5 h-3.5" />
                          <span>Edit</span>
                        </button>
                        <button
                          type="button"
                          className="btn-danger-subtle text-xs py-1 px-2.5 flex items-center gap-1.5"
                          onClick={() => onRejectAction?.(appr.bot_id, appr.id)}
                        >
                          <X className="w-3.5 h-3.5" />
                          <span>Reject</span>
                        </button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Latest Run Summaries */}
      {runs.length > 0 && (
        <div className="bot-inbox-runs">
          <div className="bot-section-title">Latest Bot Runs</div>
          <div className="bot-runs-list">
            {runs.slice(0, 3).map((r) => {
              const summary = r.summary || {};
              const isFailed = r.status === 'failed';
              return (
                <div key={r.id} className={`bot-run-card ${isFailed ? 'failed' : ''}`}>
                  <div className="bot-run-card-top">
                    <div className="flex items-center gap-2">
                      <div className="bot-run-avatar">
                        <Bot className="w-4 h-4 text-accent" />
                      </div>
                      <span className="bot-run-botname">{r.bot_name || 'VAT Bot'}</span>
                      <span className="bot-run-trigger">{r.trigger}</span>
                    </div>
                    {r.started_at && (
                      <span className="bot-run-time">
                        {new Date(r.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                  </div>

                  <div className="bot-run-headline">
                    {summary.headline || (isFailed ? r.error || 'Run encountered an issue' : 'Audit run completed successfully.')}
                  </div>

                  <div className="bot-run-meta">
                    {summary.bills_checked != null && (
                      <span className="bot-meta-tag">{summary.bills_checked} bills</span>
                    )}
                    {summary.groups_found > 0 && (
                      <span className="bot-meta-tag warning">{summary.groups_found} duplicate groups</span>
                    )}
                    {summary.findings_found > 0 && (
                      <span className="bot-meta-tag danger">{summary.findings_found} VAT issues</span>
                    )}
                    {summary.new_findings > 0 && (
                      <span className="bot-meta-tag new-badge">+{summary.new_findings} new</span>
                    )}
                    {summary.fixed_findings > 0 && (
                      <span className="bot-meta-tag success">{summary.fixed_findings} resolved</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Active Findings Section */}
      <div className="bot-inbox-findings">
        <div className="flex items-center justify-between mb-3">
          <div className="bot-section-title" style={{ margin: 0 }}>
            Active Problems ({findings.length})
          </div>
          <div className="bot-filter-tabs">
            <button
              type="button"
              className={`bot-filter-tab ${filterState === 'all' ? 'active' : ''}`}
              onClick={() => setFilterState('all')}
            >
              All
            </button>
            <button
              type="button"
              className={`bot-filter-tab ${filterState === 'new' ? 'active' : ''}`}
              onClick={() => setFilterState('new')}
            >
              New ({findings.filter((f) => f.state === 'new').length})
            </button>
            <button
              type="button"
              className={`bot-filter-tab ${filterState === 'seen' ? 'active' : ''}`}
              onClick={() => setFilterState('seen')}
            >
              Seen ({findings.filter((f) => f.state === 'seen').length})
            </button>
          </div>
        </div>

        {filteredFindings.length === 0 ? (
          <div className="bot-inbox-empty">
            <CheckCircle2 className="w-8 h-8 text-success mb-2" />
            <h4>Everything looks clear</h4>
            <p>
              {filterState === 'new'
                ? 'No unread problems right now.'
                : 'No active VAT problems detected across your bots.'}
            </p>
          </div>
        ) : (
          <div className="bot-findings-list">
            {filteredFindings.map((f) => {
              const isNew = f.state === 'new';
              const isBusy = dismissingKey === f.key;
              const ruleLabels = {
                duplicate_bill: 'Likely Duplicate Bill',
                vat_rate_wrong: 'Incorrect VAT Rate',
                bill_missing_details: 'Missing Bill Details',
                unusual_bill: 'Unusual Bill Amount / Timing',
                sales_vat_wrong: 'Sales Invoice VAT Discrepancy',
              };
              const displayRule = ruleLabels[f.rule] || f.rule || 'VAT Anomaly';

              return (
                <div key={`${f.bot_id}-${f.key}`} className={`bot-finding-card ${isNew ? 'is-new' : ''}`}>
                  <div className="bot-finding-card-header">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className={`w-4 h-4 ${isNew ? 'text-danger' : 'text-warning'}`} />
                      <span className="bot-finding-rule">{displayRule}</span>
                      {isNew && <span className="bot-badge-new">New</span>}
                    </div>
                    {f.amount != null && (
                      <span className="bot-finding-amount">
                        AED {Number(f.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </span>
                    )}
                  </div>

                  <div className="bot-finding-meta">
                    <span className="bot-meta-tag">{f.bot_name || 'VAT Watch'}</span>
                    <span className="bot-meta-tag">Org #{f.org_id}</span>
                    <span className="bot-finding-key text-ink-faint">ID: {f.key}</span>
                  </div>

                  <div className="bot-finding-actions">
                    <button
                      type="button"
                      className="btn-secondary text-xs py-1 px-2.5"
                      onClick={() => handleDismiss(f.bot_id, f.key, f.supplier || f.vendor_name)}
                      disabled={isBusy}
                      title="Mark as not a problem and teach the bot"
                    >
                      <span>{isBusy ? 'Saving…' : 'Not a problem (Dismiss)'}</span>
                    </button>
                    {onSeeFindingsInChat && (
                      <button
                        type="button"
                        className="btn-subtle text-xs py-1 px-2 flex items-center gap-1"
                        onClick={() => onSeeFindingsInChat(f)}
                        title="Investigate in VAT Agent chat"
                      >
                        <span>Investigate</span>
                        <ExternalLink className="w-3.5 h-3.5" />
                      </button>
                    )}
                    <button
                      type="button"
                      className={`btn-subtle text-xs py-1 px-2 flex items-center gap-1 ${expandedWhy[f.key] ? 'active' : ''}`}
                      onClick={() => toggleWhy(f.key)}
                      title="Why did I get this?"
                    >
                      <HelpCircle className="w-3.5 h-3.5 text-accent" />
                      <span>Why?</span>
                      {expandedWhy[f.key] ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                    </button>
                  </div>

                  {expandedWhy[f.key] && (
                    <div className="bot-finding-why-box">
                      <div className="bot-finding-why-title">
                        <Sparkles className="w-3.5 h-3.5 text-accent" />
                        <span>Why did I get this?</span>
                      </div>
                      <div className="bot-finding-why-grid">
                        <div className="bot-finding-why-row">
                          <span className="bot-finding-why-label">Bot:</span>
                          <span className="bot-finding-why-value">{f.bot_name || 'VAT Watch Bot'}</span>
                        </div>
                        <div className="bot-finding-why-row">
                          <span className="bot-finding-why-label">Watch:</span>
                          <span className="bot-finding-why-value font-medium">{f.watch_label || f.watch_id || 'Active Monitor'}</span>
                        </div>
                        <div className="bot-finding-why-row">
                          <span className="bot-finding-why-label">Rule:</span>
                          <span className="bot-finding-why-value font-mono text-xs">{f.rule}</span>
                        </div>
                        <div className="bot-finding-why-row">
                          <span className="bot-finding-why-label">Confidence:</span>
                          <span className="bot-finding-why-value">High</span>
                        </div>
                        {f.why_text && (
                          <div className="bot-finding-why-row">
                            <span className="bot-finding-why-label">Reason:</span>
                            <span className="bot-finding-why-value text-ink-muted">{f.why_text}</span>
                          </div>
                        )}
                      </div>
                      {onEditBot && f.bot_id && (
                        <button
                          type="button"
                          className="bot-finding-why-edit-btn"
                          onClick={() => onEditBot(f.bot_id)}
                          title="Open bot settings to configure this watch"
                        >
                          <Sliders className="w-3 h-3" />
                          <span>Adjust watch in bot settings &rarr;</span>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
