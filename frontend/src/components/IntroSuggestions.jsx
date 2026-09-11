import React from 'react';
import {
  BarChart3,
  ShieldAlert,
  CircleDollarSign,
  TrendingUp,
  ArrowUpRight,
  Sparkles,
} from 'lucide-react';

/** Each card and pill is a different question — no overlapping intents. */
const SUGGESTIONS = [
  {
    id: 'pnl-report',
    title: 'This year’s P&L',
    desc: 'Full income statement by month',
    prompt: 'Generate our profit and loss statement for this fiscal year, broken down by month, including revenue, cost of sales, operating expenses, and net profit.',
    icon: <BarChart3 size={15} strokeWidth={2} />,
    theme: 'teal',
  },
  {
    id: 'aged-receivables',
    title: 'Who owes us money',
    desc: 'Aged receivables by customer',
    prompt: 'Show aged receivables by customer, grouped into current, 1–30, 31–60, 61–90, and over 90 days, with outstanding balances.',
    icon: <CircleDollarSign size={15} strokeWidth={2} />,
    theme: 'amber',
  },
  {
    id: 'audit-scan',
    title: 'Audit & anomaly scan',
    desc: 'Duplicates and unusual journals',
    prompt: 'Scan recent journal entries and invoices for duplicate amounts, missing tax codes, round-number outliers, and other audit anomalies.',
    icon: <ShieldAlert size={15} strokeWidth={2} />,
    theme: 'rose',
  },
  {
    id: 'cash-forecast',
    title: '90-day cash forecast',
    desc: 'Runway from receipts and bills',
    prompt: 'Forecast cash in and cash out for the next 90 days using unpaid invoices, supplier bills coming due, and typical collection timing.',
    icon: <TrendingUp size={15} strokeWidth={2} />,
    theme: 'violet',
  },
];

const SUGGESTIVE_PILLS = [
  {
    label: 'Top customers by sales',
    prompt: 'Who are our top 5 customers by sales this year, and how much has each one been invoiced?',
  },
  {
    label: 'Supplier bills past due',
    prompt: 'Which supplier bills are overdue, who are they owed to, and what is still outstanding?',
  },
  {
    label: 'VAT collected vs reclaimable',
    prompt: 'What is our output VAT collected versus input VAT reclaimable for the current quarter?',
  },
  {
    label: 'Biggest expense accounts',
    prompt: 'Which expense accounts have the highest spend this month, and how much is each?',
  },
  {
    label: 'Cash and bank on hand',
    prompt: 'What is our current cash and bank balance across all accounts?',
  },
];

export function IntroSuggestions({ onSelectPrompt }) {
  return (
    <div className="intro-hero">
      <h1 className="intro-hero-title">
        How can I help you today?
      </h1>

      <p className="intro-hero-subtitle">
        Profitability, collections, cash, VAT, expenses, and audit checks — pick a starting point.
      </p>

      <div className="minimal-suggestion-grid">
        {SUGGESTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`minimal-card theme-${item.theme}`}
            onClick={() => onSelectPrompt(item.prompt)}
          >
            <div className="minimal-card-icon">
              {item.icon}
            </div>
            <div className="minimal-card-content">
              <span className="minimal-card-title">{item.title}</span>
              <span className="minimal-card-desc">{item.desc}</span>
            </div>
            <div className="minimal-card-arrow">
              <ArrowUpRight size={14} strokeWidth={2.2} />
            </div>
          </button>
        ))}
      </div>

      <div className="suggestive-pills-container">
        {SUGGESTIVE_PILLS.map((pill, idx) => (
          <button
            key={idx}
            type="button"
            className="suggestive-pill"
            onClick={() => onSelectPrompt(pill.prompt)}
          >
            <Sparkles size={11} className="suggestive-pill-icon" />
            <span>{pill.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
