import React from 'react';
import { AlertTriangle, Bell, CheckCircle2, FileText, Layers, ShieldCheck } from 'lucide-react';

const WATCH_LABELS = {
  duplicate_bill: 'Duplicate bills',
  vat_rate_wrong: 'Wrong VAT rate (not 0% or 5%)',
  bill_missing_details: 'Missing supplier / number or future date',
  unusual_bill: 'Unusually large bills',
  sales_vat_wrong: 'Sales invoice VAT errors',
};

export default function TryResult({ result }) {
  if (!result) return null;

  const {
    period = {},
    bills_checked = 0,
    invoices_checked = 0,
    groups_found = 0,
    findings_found = 0,
    by_watch = {},
    would_send = {},
  } = result;

  const periodLabel = period.start && period.end
    ? `${period.start} to ${period.end}`
    : 'Selected period';

  const totalProblems = groups_found + findings_found;

  return (
    <div className="try-result-card">
      <div className="try-result-header">
        <div className="try-result-icon">
          <ShieldCheck className="w-5 h-5" />
        </div>
        <div>
          <h4 className="try-result-title">Trial Run Preview</h4>
          <p className="try-result-period">Period: {periodLabel}</p>
        </div>
      </div>

      <div className="try-result-grid">
        <div className="try-stat-box">
          <span className="try-stat-label">Bills checked</span>
          <span className="try-stat-val">{bills_checked}</span>
          {invoices_checked > 0 && (
            <span className="try-stat-sub">+{invoices_checked} sales invoices</span>
          )}
        </div>

        <div className="try-stat-box">
          <span className="try-stat-label">Likely duplicates</span>
          <span className="try-stat-val text-warning">{groups_found}</span>
          <span className="try-stat-sub">groups</span>
        </div>

        <div className="try-stat-box">
          <span className="try-stat-label">Other problems</span>
          <span className="try-stat-val text-danger">{findings_found}</span>
          <span className="try-stat-sub">findings</span>
        </div>

        <div className="try-stat-box highlight">
          <span className="try-stat-label">Would have sent</span>
          <span className="try-stat-val text-accent">
            {would_send.alerts || totalProblems} alerts
          </span>
          <span className="try-stat-sub">{would_send.digests || 0} digests/mo</span>
        </div>
      </div>

      {Object.keys(by_watch).length > 0 && (
        <div className="try-breakdown">
          <span className="try-breakdown-title">Problems found by check:</span>
          <div className="try-watch-list">
            {Object.entries(by_watch).map(([watchId, count]) => (
              <div key={watchId} className="try-watch-item">
                <span className="try-watch-name">
                  {WATCH_LABELS[watchId] || watchId}
                </span>
                <span className={`try-watch-count ${count > 0 ? 'active' : ''}`}>
                  {count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {totalProblems === 0 && (
        <div className="try-clean-banner">
          <CheckCircle2 className="w-4 h-4 text-success" />
          <span>Everything looks clean! No issues detected for this period.</span>
        </div>
      )}
    </div>
  );
}
