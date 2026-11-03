import React, { useEffect, useState } from 'react';
import { AlertTriangle, Bot, Calendar, Clock, Inbox, Layers, Pause, Play, Plus, ShieldCheck, Sparkles } from 'lucide-react';
import { fetchBotTemplates } from '../../services/bots';
import { agentMeta, allBotAgents, botStatus, botSummary, credits, longDate, periodLabel, runText, runTime, shortDay } from './botText';
import './bots.css';
import './bots-pages.css';

const TEMPLATE_ICONS = { vat_weekly_digest: Calendar, vat_return_reminder: Clock, vat_full_watch: Layers,
  ap_weekly_check: Calendar, ap_pay_run_prep: Clock, ap_daily_watch: Layers };

/** The bot card's icon: a calendar for reminder-only bots, a shield for the rest. */
function BotIcon({ bot }) {
  const reminderOnly = (bot.draft?.schedules || []).every((s) => s.kind === 'before_deadline');
  const Icon = reminderOnly && (bot.draft?.schedules || []).length ? Calendar : ShieldCheck;
  return <span className={`bh-bot-icon is-${bot.status}`}><Icon size={18} /></span>;
}

function StatusBadge({ bot }) {
  const { label, tone } = botStatus(bot);
  return <span className={`bh-badge is-${tone}`}><span className="bh-badge-dot" />{label}</span>;
}

/** The box in the middle of a bot card: what happened last, or why it is not running. */
function LastRun({ bot }) {
  const run = bot.overview?.last_run;
  if (bot.status === 'paused') {
    return (
      <div className="bh-lastrun">
        <p>Paused. It won't run or use credits until you resume it.</p>
      </div>
    );
  }
  if (bot.status === 'error') {
    return (
      <div className="bh-lastrun is-attention">
        <AlertTriangle size={14} />
        <p>{bot.status_note || 'Something stopped this bot. Open it to see why.'}</p>
      </div>
    );
  }
  if (!run) {
    return (
      <div className="bh-lastrun">
        <p className="bh-lastrun-label">Waiting for its first run</p>
        <p>{bot.next_run_at ? `It runs ${runTime(bot.next_run_at)}.` : 'It has no run planned yet.'}</p>
      </div>
    );
  }
  return (
    <div className={`bh-lastrun${run.status === 'failed' ? ' is-attention' : ''}`}>
      <p className="bh-lastrun-label">Last run · {shortDay(run.started_at)}</p>
      <p>{runText(run)}</p>
    </div>
  );
}

function BotCard({ bot, onOpen, onToggle, onEdit }) {
  const paused = bot.status === 'paused';
  return (
    <article className={`bh-card is-${bot.status}`}>
      <header className="bh-card-head">
        <BotIcon bot={bot} />
        <div className="bh-card-title">
          <div className="bh-card-name-row">
            <h3>{bot.name}</h3>
            <StatusBadge bot={bot} />
          </div>
          <p className="bh-card-sub">{botSummary(bot.draft)}</p>
        </div>
      </header>
      <LastRun bot={bot} />
      <footer className="bh-card-foot">
        <span className="bh-next">
          {paused
            ? (bot.last_run_at ? `Last run: ${shortDay(bot.last_run_at)}` : 'Not run yet')
            : (bot.next_run_at ? `Next: ${runTime(bot.next_run_at)}` : 'No run planned')}
        </span>
        <div className="bh-card-actions">
          {bot.status === 'error' ? (
            <button type="button" className="bh-btn is-primary" onClick={() => onEdit(bot)}>Review organizations</button>
          ) : (
            <>
              <button
                type="button"
                className={`bh-btn${paused ? '' : ' is-icon'}`}
                onClick={() => onToggle(bot.id)}
                title={paused ? 'Resume' : 'Pause'}
                aria-label={paused ? `Resume ${bot.name}` : `Pause ${bot.name}`}
              >
                {paused ? <><Play size={13} /> Resume</> : <Pause size={13} />}
              </button>
              <button type="button" className="bh-btn" onClick={() => onOpen(bot.id)}>Open</button>
            </>
          )}
        </div>
      </footer>
    </article>
  );
}

function money(n, currency = 'AED') {
  return `${currency} ${Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function Stat({ label, value, note, tone }) {
  return (
    <div className="bh-stat">
      <span className="bh-stat-label">{label}</span>
      <span className="bh-stat-value">{value}</span>
      <span className={`bh-stat-note${tone ? ` is-${tone}` : ''}`}>{note}</span>
    </div>
  );
}

/**
 * The Bots home: what is coming up, what is open, every bot with its last run, and templates to start from.
 * `bots` carry `overview` and `monthly_credits` (GET /api/v1/bots); `summary` is GET /api/v1/bots/summary.
 */
export default function BotsHome({ bots = [], summary, token = '', inboxNew = 0, onOpenInbox, onNewBot, onUseTemplate,
  onOpenBot, onToggleBot, onEditBot }) {
  const [templates, setTemplates] = useState([]);

  // Every agent's templates, in agent order.
  const agentKeys = allBotAgents().map((a) => a.key).join(',');
  useEffect(() => {
    Promise.all(agentKeys.split(',').map((k) => fetchBotTemplates(k, token).then((r) => r.templates || []).catch(() => [])))
      .then((lists) => setTemplates(lists.flat()));
  }, [token, agentKeys]);

  // Each agent's cards from the summary; the old VAT-only fields are the fallback.
  const cards = summary?.cards || {};
  const cardOf = (agent, kind) => (cards[agent] || []).find((c) => c.kind === kind);
  const openCards = Object.keys(cards).map((a) => cardOf(a, 'open_problems')).filter(Boolean);
  const open = openCards.length
    ? openCards.reduce((t, c) => ({ total: t.total + c.total, new: t.new + c.new, duplicates: t.duplicates + c.duplicates,
      other: t.other + c.other }), { total: 0, new: 0, duplicates: 0, other: 0 })
    : summary?.open_problems;
  const problemWord = Object.keys(cards).length > 1 ? 'problem' : (agentMeta(Object.keys(cards)[0] || 'vat').copy?.problem_word || 'problem');
  const ret = summary?.next_return;
  const payables = cardOf('ap', 'payables')?.position;
  const showReturn = Boolean(cards.vat) || !cards.ap;
  const reminder = bots.find((b) => b.status === 'active' && (b.draft?.schedules || []).some((s) => s.kind === 'before_deadline'));
  const max = summary?.max_bots || 10;

  return (
    <div className="bots-page">
      <div className="bh-head">
        <div>
          <h1 className="bots-title">Bots</h1>
          <p className="bots-lede">
            Assistants that check your books on a schedule and tell you what needs fixing. They read only: a bot never
            changes a bill or files a return.
          </p>
        </div>
        <div className="bh-head-actions">
          <button type="button" className="bh-btn" onClick={onOpenInbox}>
            <Inbox size={14} /> Inbox
            {inboxNew > 0 && <span className="bh-pill is-danger">{inboxNew} new</span>}
          </button>
          <button type="button" className="bh-btn is-primary" onClick={onNewBot} disabled={bots.length >= max}>
            <Plus size={14} /> New bot
          </button>
        </div>
      </div>

      <div className="bh-stats">
        <Stat
          label="Next run"
          value={summary?.next_run ? runTime(summary.next_run.at) : '—'}
          note={summary?.next_run ? summary.next_run.bot_name : 'No bot is waiting to run'}
        />
        <Stat
          label="Open problems"
          value={open ? <>{open.total}{open.new > 0 && <span className="bh-stat-new"> · {open.new} new</span>}</> : '—'}
          note={open ? `${open.duplicates} possible duplicate${open.duplicates === 1 ? '' : 's'} · ${open.other} other ${problemWord}${open.other === 1 ? '' : 's'}` : ''}
          tone={open?.new ? 'warning' : undefined}
        />
        {showReturn && (
          <Stat
            label="Next VAT return"
            value={ret ? `${periodLabel(ret.period)} · due ${longDate(ret.due_date)}` : '—'}
            note={reminder?.next_run_at ? `First reminder ${shortDay(reminder.next_run_at)}` : ret ? `${ret.days_to_due} days to go` : ''}
          />
        )}
        {cards.ap && (
          <Stat
            label="Bills due in 7 days"
            value={payables ? `${money(payables.due_next_7_days, payables.currency)} · ${payables.due_next_7_days_count}` : '—'}
            note={payables ? `${money(payables.overdue, payables.currency)} overdue · ${payables.overdue_count} bill${payables.overdue_count === 1 ? '' : 's'}` : ''}
            tone={payables?.overdue_count ? 'warning' : undefined}
          />
        )}
        <Stat
          label="Bot usage"
          value={summary ? `≈ ${credits(summary.credits.estimate_per_month)} credits / month` : '—'}
          note={summary ? `${credits(summary.credits.this_month)} used this month` : ''}
        />
      </div>

      <div className="bh-section-head">
        <h2>Your bots</h2>
        <span className="bh-pill">{bots.length} of {max}</span>
      </div>
      <div className="bh-grid">
        {bots.map((b) => (
          <BotCard key={b.id} bot={b} onOpen={onOpenBot} onToggle={onToggleBot} onEdit={onEditBot} />
        ))}
        {bots.length < max && (
          <button type="button" className="bh-card bh-create" onClick={onNewBot}>
            <span className="bh-create-icon"><Plus size={18} /></span>
            <span className="bh-create-title">Create a bot</span>
            <span className="bh-create-sub">Describe it in one sentence, or start from a template.</span>
          </button>
        )}
      </div>

      <div className="bh-section-head">
        <h2>Start from a template</h2>
        <span className="bh-section-note">Ready in one click. You can change everything before turning it on.</span>
      </div>
      <div className="bh-templates">
        {templates.map((t) => {
          const Icon = TEMPLATE_ICONS[t.id] || Sparkles;
          return (
            <button key={t.id} type="button" className="bh-template" onClick={() => onUseTemplate(t.id)}
              disabled={bots.length >= max}>
              <span className="bh-template-icon"><Icon size={16} /></span>
              <span className="bh-template-title">{t.title}</span>
              <span className="bh-template-sub">{t.summary}</span>
              {t.monthly_credits != null && <span className="bh-template-cost">≈ {credits(t.monthly_credits)} credits / month</span>}
            </button>
          );
        })}
        <div className="bh-template is-soon">
          <span className="bh-template-icon"><Bot size={16} /></span>
          <span className="bh-template-title">More agents <span className="bh-pill">Soon</span></span>
          <span className="bh-template-sub">Collections and cash bots will appear here.</span>
        </div>
      </div>
    </div>
  );
}
