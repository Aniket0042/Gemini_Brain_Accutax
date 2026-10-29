import React from 'react';
import { Bot, CheckCircle, ChevronRight, Sliders, Sparkles } from 'lucide-react';

function scheduleWords(schedules = []) {
  if (!schedules.length) return 'on schedule';
  const parts = schedules.map((s) => {
    if (s.kind === 'weekly') return `every ${s.day || 'week'} at ${s.time || '08:00'}`;
    if (s.kind === 'monthly') return `monthly on day ${s.day_of_month || 1} at ${s.time || '08:00'}`;
    if (s.kind === 'before_deadline') return `before each VAT return deadline`;
    if (s.kind === 'after_period_end') return `after each quarter ends`;
    return 'regularly';
  });
  return parts.join(' and ');
}

function deliverWords(deliver = ['inbox']) {
  const labels = { inbox: 'your Inbox', email: 'email', bell: 'notifications' };
  return deliver.map((d) => labels[d] || d).join(' and ');
}

export default function BotIntro({ bot, result, onSeeFindings, onAdjust }) {
  if (!bot) return null;

  const { draft } = bot;
  const name = draft?.name || 'VAT Watch';
  const bills = result?.bills_checked || 0;
  const invoices = result?.invoices_checked || 0;
  const groups = result?.groups_found || 0;
  const findings = result?.findings_found || 0;
  const period = result?.period?.start && result?.period?.end
    ? `${result.period.start} to ${result.period.end}`
    : 'this quarter';

  const totalProblems = groups + findings;
  const schedDesc = scheduleWords(draft?.schedules);
  const deliverDesc = deliverWords(draft?.deliver);

  return (
    <div className="bot-intro-card">
      <div className="bot-intro-badge">
        <Sparkles className="w-4 h-4 text-accent" />
        <span>Bot is now active</span>
      </div>

      <div className="bot-intro-header">
        <div className="bot-intro-avatar">
          <Bot className="w-6 h-6 text-accent" />
        </div>
        <div>
          <h3 className="bot-intro-name">Hello, I'm {name}</h3>
          <p className="bot-intro-sub">Your personal VAT assistant</p>
        </div>
      </div>

      <div className="bot-intro-body">
        {totalProblems > 0 ? (
          <p>
            I checked <strong>{bills} bills</strong>
            {invoices > 0 ? ` and ${invoices} sales invoices` : ''} for {period}. I found{' '}
            <strong className="text-warning">{groups} likely duplicate groups</strong> and{' '}
            <strong className="text-danger">{findings} other VAT problems</strong>.
            <br />
            I'll run <strong>{schedDesc}</strong> and send verified reports to{' '}
            <strong>{deliverDesc}</strong>.
          </p>
        ) : (
          <p>
            I checked <strong>{bills} bills</strong>
            {invoices > 0 ? ` and ${invoices} sales invoices` : ''} for {period}. Everything looks right!
            <br />
            I'll watch your books <strong>{schedDesc}</strong> and send updates to{' '}
            <strong>{deliverDesc}</strong>.
          </p>
        )}
      </div>

      <div className="bot-intro-actions">
        {totalProblems > 0 && onSeeFindings && (
          <button
            type="button"
            className="btn-primary flex items-center gap-2"
            onClick={onSeeFindings}
          >
            <span>See findings</span>
            <ChevronRight className="w-4 h-4" />
          </button>
        )}

        {onAdjust && (
          <button
            type="button"
            className="btn-secondary flex items-center gap-2"
            onClick={onAdjust}
          >
            <Sliders className="w-4 h-4" />
            <span>Adjust settings</span>
          </button>
        )}
      </div>
    </div>
  );
}
