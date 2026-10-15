import React from 'react';
import { AlertTriangle, ExternalLink, Info } from 'lucide-react';

/** A number as people write it: 1,293.48. Text from the server (a Decimal as a string) is read as a number. */
export function formatAmount(value) {
  const n = typeof value === 'string' ? Number(value) : value;
  if (n === null || n === undefined || Number.isNaN(n)) return '';
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** "1 Mar 2026" from "2026-03-01"; the text itself when it is not a date. */
export function formatDay(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!m) return value || '';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1]}`;
}

function Chip({ level, children }) {
  return <span className={`vr-chip vr-level-${level || 'none'}`}>{children}</span>;
}

function Summary({ block }) {
  const s = block.summary || {};
  const chips = [];
  if (s.duplicate_groups) chips.push(<Chip key="dup" level="medium">{s.duplicate_groups} possible {s.duplicate_groups === 1 ? 'duplicate' : 'duplicates'}</Chip>);
  if (s.high) chips.push(<Chip key="high" level="high">{s.high} high</Chip>);
  if (s.medium) chips.push(<Chip key="med" level="medium">{s.medium} medium</Chip>);
  if (s.low) chips.push(<Chip key="low" level="low">{s.low} low</Chip>);
  if (!chips.length) return <Chip level="good">Nothing found by these rules</Chip>;
  return chips;
}

function BillRow({ bill, currency }) {
  return (
    <li className="vr-bill">
      {bill.url
        ? <a href={bill.url} target="_blank" rel="noreferrer" className="vr-bill-number">{bill.number}<ExternalLink size={11} aria-hidden="true" /></a>
        : <span className="vr-bill-number">{bill.number}</span>}
      <span className="vr-bill-date">{formatDay(bill.date)}</span>
      <span className="vr-bill-supplier" title={bill.supplier || ''}>{bill.supplier}</span>
      <span className="vr-bill-amount">{currency ? `${currency} ` : ''}{formatAmount(bill.amount)}</span>
    </li>
  );
}

function Item({ item, showOrganization }) {
  return (
    <article className={`vr-item vr-level-${item.level || 'none'}`}>
      <header className="vr-item-head">
        <Chip level={item.level}>{item.level_word}</Chip>
        <h4 className="vr-item-title">{item.title}</h4>
        {showOrganization && item.organization && <span className="vr-item-org">{item.organization}</span>}
      </header>
      {item.why && <p className="vr-item-why">{item.why}</p>}
      <ul className="vr-bills">
        {(item.bills || []).map((b, i) => <BillRow key={`${b.number}-${i}`} bill={b} currency={item.currency} />)}
      </ul>
      {item.more > 0 && <p className="vr-more">and {item.more} more {item.more === 1 ? 'bill' : 'bills'} in this group</p>}
      {item.kind === 'duplicate' && item.extra_amount > 0 && (
        <p className="vr-extra">{item.currency ? `${item.currency} ` : ''}{formatAmount(item.extra_amount)} would be counted twice if these are repeats.</p>
      )}
    </article>
  );
}

/** What a VAT review of the books found: totals, patterns, then each duplicate group and odd bill with its bills. */
export function VatReviewBlock({ block }) {
  const items = block.items || [];
  const organizations = new Set(items.map((i) => i.organization).filter(Boolean));
  const period = block.period ? `${formatDay(block.period.start)} to ${formatDay(block.period.end)}` : '';
  return (
    <section className="vr-card" aria-label="VAT review">
      <header className="vr-head">
        <h3 className="vr-title"><AlertTriangle size={15} aria-hidden="true" />VAT review</h3>
        <span className="vr-sub">{period}{period && block.bills_checked != null ? ' · ' : ''}{block.bills_checked != null ? `${block.bills_checked.toLocaleString('en-US')} bills checked` : ''}</span>
        <div className="vr-summary"><Summary block={block} /></div>
      </header>
      {(block.patterns || []).map((text, i) => (
        <p key={i} className="vr-pattern"><Info size={14} aria-hidden="true" />{text}</p>
      ))}
      {items.length > 0 && (
        <div className="vr-items">
          {items.map((item, i) => <Item key={i} item={item} showOrganization={organizations.size > 1} />)}
        </div>
      )}
      {(block.notes || []).map((text, i) => <p key={i} className="vr-note">{text}</p>)}
    </section>
  );
}
