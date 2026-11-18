import React, { useState } from 'react';
import { FileText, Copy, Check, ChevronDown, ChevronRight, ExternalLink, Info } from 'lucide-react';
import { formatAmount, formatDay } from './VatReviewBlock';

const STATUS_TAGS = {
  from_books: { text: 'From books', level: 'good' },
  approximate: { text: 'Approximate', level: 'medium' },
  not_in_books: { text: 'Not in books', level: 'none' },
  candidates_only: { text: 'To confirm', level: 'medium' },
  needs_emirate: { text: 'Needs emirate', level: 'high' },
  incomplete: { text: 'Incomplete', level: 'high' },
  unavailable: { text: 'Not available', level: 'high' },
};

function StatusTag({ status }) {
  const meta = STATUS_TAGS[status] || { text: status || 'Unknown', level: 'none' };
  return <span className={`vr-chip vr-level-${meta.level}`}>{meta.text}</span>;
}

function CopyButton({ value, copyKey, copiedKey, onCopy }) {
  if (value === null || value === undefined) return <span style={{ color: 'var(--ink-faint)' }}>—</span>;
  const isCopied = copiedKey === copyKey;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontVariantNumeric: 'tabular-nums' }}>
      <span>{formatAmount(value)}</span>
      <button
        type="button"
        title="Copy exact amount"
        aria-label="Copy exact amount"
        onClick={() => onCopy(copyKey, value)}
        style={{
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          padding: '2px 4px',
          borderRadius: '4px',
          color: isCopied ? 'var(--success-ink)' : 'var(--ink-faint)',
          display: 'inline-flex',
          alignItems: 'center',
        }}
      >
        {isCopied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
      </button>
    </span>
  );
}

function SectionTable({ title, boxes, orgId, copiedKey, onCopy }) {
  if (!boxes.length) return null;
  return (
    <div style={{ marginTop: '10px' }}>
      <h4 style={{ margin: '0 0 6px 0', fontSize: 'var(--text-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--ink-soft)' }}>
        {title}
      </h4>
      <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-xs)', minWidth: '460px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left', color: 'var(--ink-faint)' }}>
              <th style={{ padding: '6px 8px', width: '56px' }}>Box</th>
              <th style={{ padding: '6px 8px' }}>Description</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', width: '120px' }}>Net (AED)</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', width: '120px' }}>VAT (AED)</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', width: '90px' }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {boxes.map((b) => {
              const netKey = `${orgId}-${b.box}-net`;
              const vatKey = `${orgId}-${b.box}-vat`;
              const isTotal = ['8', '11', '12', '13', '14'].includes(String(b.box));
              return (
                <tr
                  key={b.box}
                  style={{
                    borderBottom: '1px solid var(--border-soft)',
                    background: isTotal ? 'var(--surface-2)' : 'transparent',
                    fontWeight: isTotal ? 600 : 400,
                  }}
                >
                  <td style={{ padding: '6px 8px', color: 'var(--ink)' }}>{b.box.startsWith('1') && b.box !== '11' && b.box !== '12' && b.box !== '13' && b.box !== '14' ? `Box ${b.box}` : b.box === 'unassigned' ? 'Unassigned' : `Box ${b.box}`}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--ink)' }}>
                    {b.label}
                    {b.note && <div style={{ fontSize: '11px', color: 'var(--ink-faint)', marginTop: '2px' }}>{b.note}</div>}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    <CopyButton value={b.net} copyKey={netKey} copiedKey={copiedKey} onCopy={onCopy} />
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    <CopyButton value={b.vat} copyKey={vatKey} copiedKey={copiedKey} onCopy={onCopy} />
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    <StatusTag status={b.status} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ReverseChargeSection({ rc, currency }) {
  const [open, setOpen] = useState(false);
  const candidates = rc?.candidates || [];
  const count = rc?.candidate_count || candidates.length;
  const ifConfirmed = rc?.if_confirmed;

  return (
    <div style={{ marginTop: '12px', border: '1px solid var(--border-soft)', borderRadius: 'var(--radius-sm)', background: 'var(--surface-2)', padding: '10px 12px' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'none',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          color: 'var(--ink)',
          fontSize: 'var(--text-sm)',
          fontWeight: 600,
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Info size={14} color="var(--warning-ink)" aria-hidden="true" />
          Bills that may need reverse charge ({count})
        </span>
        {open ? <ChevronDown size={15} aria-hidden="true" /> : <ChevronRight size={15} aria-hidden="true" />}
      </button>

      {ifConfirmed && (
        <div style={{ marginTop: '6px', fontSize: 'var(--text-xs)', color: 'var(--ink-soft)', lineHeight: 1.45 }}>
          <strong>If confirmed:</strong> Box 3 +AED {formatAmount(ifConfirmed.box3_net)} net / +AED {formatAmount(ifConfirmed.box3_vat)} output VAT · Box 10 +AED {formatAmount(ifConfirmed.box10_net)} net / +AED {formatAmount(ifConfirmed.box10_vat)} recoverable VAT · Net change if the VAT is fully recoverable: AED {formatAmount(ifConfirmed.net_change)}. If some of it is blocked (Article 53), Box 10 is lower and VAT becomes payable.
        </div>
      )}

      {open && (
        <div style={{ marginTop: '10px' }}>
          {candidates.length > 0 ? (
            <ul className="vr-bills" style={{ margin: 0, padding: 0 }}>
              {candidates.map((b, i) => (
                <li key={i} className="vr-bill" style={{ borderBottom: '1px solid var(--border-soft)', paddingBottom: '4px' }}>
                  {b.url ? (
                    <a href={b.url} target="_blank" rel="noreferrer" className="vr-bill-number">
                      {b.document_number || 'Bill'}
                      <ExternalLink size={11} aria-hidden="true" />
                    </a>
                  ) : (
                    <span className="vr-bill-number">{b.document_number || 'Bill'}</span>
                  )}
                  <span className="vr-bill-date">{formatDay(b.document_date)}</span>
                  <span className="vr-bill-supplier" title={b.vendor_name || ''}>
                    {b.vendor_name} {b.country ? `(${b.country})` : ''}
                  </span>
                  <span className="vr-bill-amount">
                    AED {formatAmount(b.net)} (VAT: {formatAmount(b.self_assessed_vat)})
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ margin: '4px 0', fontSize: 'var(--text-xs)', color: 'var(--ink-faint)' }}>No reverse-charge candidates in this period.</p>
          )}
          {rc?.note && (
            <p style={{ margin: '8px 0 0', fontSize: '11px', color: 'var(--ink-faint)', lineHeight: 1.4 }}>
              {rc.note}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function Vat201Block({ block }) {
  const [copiedKey, setCopiedKey] = useState(null);

  const handleCopy = (key, val) => {
    if (val === null || val === undefined) return;
    const numStr = Number(val).toFixed(2);
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(numStr);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 1500);
    }
  };

  const orgs = block?.organizations || [];

  return (
    <section className="vr-card" aria-label="VAT 201 Draft">
      <header className="vr-head" style={{ marginBottom: '8px' }}>
        <h3 className="vr-title">
          <FileText size={15} aria-hidden="true" color="var(--accent)" />
          {block.title || 'VAT 201 draft'}
        </h3>
        <span className="vr-chip vr-level-good">Official Statutory Layout</span>
      </header>

      {orgs.map((org) => {
        const boxes = org.boxes || [];
        const isBox1 = (b) => b.box.startsWith('1') && !['10', '11', '12', '13', '14'].includes(b.box) || b.box === 'unassigned';
        const outputBoxes = boxes.filter((b) => isBox1(b) || ['2', '3', '4', '5', '6', '7', '8'].includes(String(b.box)));
        const inputBoxes = boxes.filter((b) => ['9', '10', '11'].includes(String(b.box)));
        const netBoxes = boxes.filter((b) => ['12', '13', '14'].includes(String(b.box)));

        return (
          <div key={org.organization_id} style={{ marginBottom: '14px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'baseline', color: 'var(--ink-soft)', fontSize: 'var(--text-xs)', marginBottom: '6px' }}>
              {org.emirate && <span><strong>Emirate:</strong> {org.emirate}</span>}
              {org.frequency && <span><strong>Frequency:</strong> {org.frequency}</span>}
              {org.due_date && <span><strong>Filing deadline:</strong> {formatDay(org.due_date)}</span>}
            </div>

            <SectionTable
              title="Section 1: VAT on Sales & All Other Outputs"
              boxes={outputBoxes}
              orgId={org.organization_id}
              copiedKey={copiedKey}
              onCopy={handleCopy}
            />

            <SectionTable
              title="Section 2: VAT on Expenses & All Other Inputs"
              boxes={inputBoxes}
              orgId={org.organization_id}
              copiedKey={copiedKey}
              onCopy={handleCopy}
            />

            <SectionTable
              title="Section 3: Net VAT Due or Recoverable"
              boxes={netBoxes}
              orgId={org.organization_id}
              copiedKey={copiedKey}
              onCopy={handleCopy}
            />

            {org.reverse_charge && <ReverseChargeSection rc={org.reverse_charge} />}

            {(org.notes || []).map((text, idx) => (
              <p key={idx} className="vr-note">{text}</p>
            ))}
          </div>
        );
      })}

      <footer style={{ marginTop: '12px', paddingTop: '8px', borderTop: '1px solid var(--border-soft)', fontSize: '11px', color: 'var(--ink-faint)', lineHeight: 1.4 }}>
        {block.disclaimer || 'Draft prepared by Accutax from your books. Not an FTA document. Check every figure before filing.'}
      </footer>
    </section>
  );
}
