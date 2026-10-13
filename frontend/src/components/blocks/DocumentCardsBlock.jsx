import React from 'react';
import { AlertTriangle, Check, ChevronRight, File, FileImage, FileSpreadsheet, FileText, Minus } from 'lucide-react';
import { kindGroup } from '../../services/attachments';

const ICONS = { pdf: FileText, docx: FileText, image: FileImage, xlsx: FileSpreadsheet, csv: FileSpreadsheet };

/** The big figures first, as a row of facts; everything else is a quiet two-column list. */
const HEADLINE = ['Total', 'Tax', 'Closing balance', 'Opening balance'];

function StatusPill({ item }) {
  const checks = item.checks || [];
  if (!checks.length) return null;
  const problems = checks.filter((c) => c.status === 'fail').length;
  const notChecked = checks.filter((c) => c.status === 'na').length;
  if (problems) {
    return <span className="doc-pill is-bad"><AlertTriangle size={12} aria-hidden="true" />{problems} {problems === 1 ? 'problem' : 'problems'}</span>;
  }
  if (notChecked && notChecked === checks.length) {
    return <span className="doc-pill"><Minus size={12} aria-hidden="true" />Not checked</span>;
  }
  return <span className="doc-pill is-good"><Check size={12} aria-hidden="true" />No problems</span>;
}

function Card({ item, onPreviewFile }) {
  const Icon = ICONS[kindGroup(item.kind)] || File;
  const rows = item.rows || [];
  const headline = rows.filter((r) => HEADLINE.includes(r.label));
  const rest = rows.filter((r) => !HEADLINE.includes(r.label));
  const problems = (item.checks || []).filter((c) => c.status === 'fail');
  const fine = (item.checks || []).filter((c) => c.status === 'pass');
  const notChecked = (item.checks || []).filter((c) => c.status === 'na');

  return (
    <article className={`doc-card${problems.length ? ' has-problems' : ''}`}>
      <header className="doc-card-head">
        <button type="button" className="doc-card-file" onClick={() => onPreviewFile && onPreviewFile(item)}
                title="Open the file" aria-label={`Open ${item.name}`}>
          <Icon size={15} aria-hidden="true" />
          <span className="doc-card-name">{item.name}</span>
          <ChevronRight size={13} aria-hidden="true" />
        </button>
        <span className="doc-card-type">{item.type}</span>
        <StatusPill item={item} />
      </header>

      {headline.length > 0 && (
        <dl className="doc-card-headline">
          {headline.map((r) => (
            <div key={r.label}><dt>{r.label}</dt><dd>{r.value}</dd></div>
          ))}
        </dl>
      )}
      {rest.length > 0 && (
        <dl className="doc-card-rest">
          {rest.map((r) => (
            <div key={r.label}><dt>{r.label}</dt><dd title={r.value}>{r.value}</dd></div>
          ))}
        </dl>
      )}

      {problems.length > 0 && (
        <ul className="doc-card-problems">
          {problems.map((c) => (
            <li key={c.label}><AlertTriangle size={13} aria-hidden="true" /><span><b>{c.label}.</b> {c.detail}</span></li>
          ))}
        </ul>
      )}
      {(fine.length > 0 || notChecked.length > 0) && (
        <details className="doc-card-more">
          <summary>{fine.length} fine{notChecked.length ? `, ${notChecked.length} not checked` : ''}</summary>
          <ul>
            {fine.map((c) => <li key={c.label} className="is-fine"><Check size={12} aria-hidden="true" />{c.label}</li>)}
            {notChecked.map((c) => (
              <li key={c.label} className="is-na"><Minus size={12} aria-hidden="true" />{c.label}: {c.detail}</li>
            ))}
          </ul>
        </details>
      )}
      {item.note && <p className="doc-card-note">{item.note}</p>}
    </article>
  );
}

/**
 * DocumentCardsBlock — one compact card for each file attached to the question (block type "documents"): what it is,
 * its main figures, and what code checked on it. The answer text gives the verdict; the details live here.
 */
export function DocumentCardsBlock({ block, onPreviewFile }) {
  const items = Array.isArray(block?.items) ? block.items : [];
  if (!items.length) return null;
  return (
    <div className="doc-cards" role="list" aria-label="Attached files">
      {items.map((item) => (
        <div role="listitem" key={item.id}><Card item={item} onPreviewFile={onPreviewFile} /></div>
      ))}
    </div>
  );
}
