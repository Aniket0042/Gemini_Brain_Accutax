import React, { useState } from 'react';
import { Lock, ChevronDown, ChevronUp, Eye, EyeOff } from 'lucide-react';

// The privacy badge under an answer (PII masking on). It shows the user only their own question and answer:
// how many names and IDs the AI never saw, which ones in this Q&A, and the question and answer as the AI saw them.

const KIND_LABEL = {
  SUPPLIER: 'Supplier', CUSTOMER: 'Customer', PARTY: 'Party', PERSON: 'Person', ORG: 'Organization', TRN: 'TRN',
  EMAIL: 'Email', PHONE: 'Phone', IBAN: 'IBAN', ADDRESS: 'Address', EMIRATES_ID: 'Emirates ID', CARD: 'Card',
  PASSPORT: 'Passport', GSTIN: 'GSTIN', PAN: 'PAN', AADHAAR: 'Aadhaar', UPI_ID: 'UPI ID',
};
const TOKEN_RX = /\\?\[\s*([A-Z_]+)[ _-]?\d+\s*\\?\]/g;

// Placeholders in amber, the rest as plain text.
const withTokens = (text) => {
  const out = [];
  let last = 0;
  let m;
  TOKEN_RX.lastIndex = 0;
  while ((m = TOKEN_RX.exec(text || '')) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<mark key={m.index} style={styles.tok}>{m[0]}</mark>);
    last = m.index + m[0].length;
  }
  if (last < (text || '').length) out.push(text.slice(last));
  return out;
};

export const PrivacyBadge = ({ block }) => {
  const [open, setOpen] = useState(false);
  const [aiView, setAiView] = useState(false);
  if (!block) return null;
  const total = block.total_hidden || 0;
  const items = block.items || [];
  const label = total === 0
    ? 'No names or IDs to hide in this answer'
    : `${total} ${total === 1 ? 'name or ID' : 'names and IDs'} hidden from the AI`;

  return (
    <div style={styles.wrap}>
      <button type="button" style={styles.badge} onClick={() => setOpen((v) => !v)} aria-expanded={open}
        title="The AI model answered with placeholders; real names and IDs were put back on our server.">
        <Lock size={12} />
        <span>{label}</span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>

      {open && (
        <div style={styles.panel}>
          <p style={styles.note}>
            The AI model never received these. It worked with placeholders, and the real values were put back here
            before you saw the answer. Amounts and dates stay visible so the figures can be checked.
          </p>
          {items.length > 0 ? (
            <table style={styles.table}>
              <thead>
                <tr><th style={styles.th}>In your question and answer</th><th style={styles.th}>The AI saw</th></tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.token}>
                    <td style={styles.td}>
                      <span style={styles.kind}>{KIND_LABEL[it.kind] || it.kind}</span> {it.value}
                    </td>
                    <td style={styles.td}><mark style={styles.tok}>{it.token}</mark></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p style={styles.note}>None of the hidden values appear in this question or answer.</p>
          )}
          {total > items.length && (
            <p style={styles.note}>
              {total - items.length} more {total - items.length === 1 ? 'value' : 'values'} from your books were hidden
              while the AI worked (tool results it read but the answer does not name).
            </p>
          )}

          <button type="button" style={styles.link} onClick={() => setAiView((v) => !v)}>
            {aiView ? <EyeOff size={12} /> : <Eye size={12} />}
            <span>{aiView ? 'Hide what the AI saw' : 'Show what the AI saw'}</span>
          </button>
          {aiView && (
            <div style={styles.aiView}>
              <div style={styles.aiLabel}>Your question, as the AI received it</div>
              <pre style={styles.pre}>{withTokens(block.question)}</pre>
              <div style={styles.aiLabel}>The AI's answer, as it wrote it</div>
              <pre style={styles.pre}>{withTokens(block.answer)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const styles = {
  wrap: { marginTop: 10 },
  badge: {
    display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--success-ink)',
    background: 'var(--success-soft)', border: '1px solid transparent', borderRadius: 99, padding: '3px 10px',
    cursor: 'pointer', font: 'inherit', fontWeight: 500,
  },
  panel: { marginTop: 8, border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px', background: 'var(--surface)' },
  note: { margin: '0 0 8px', fontSize: 12.5, color: 'var(--ink-soft)', lineHeight: 1.5 },
  table: { borderCollapse: 'collapse', width: '100%', fontSize: 12.5, marginBottom: 8 },
  th: { textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--ink-faint)', padding: '4px 6px', borderBottom: '1px solid var(--border)' },
  td: { padding: '5px 6px', borderBottom: '1px solid var(--border-soft)', color: 'var(--ink)' },
  kind: { fontSize: 11, color: 'var(--ink-faint)', marginRight: 4 },
  tok: { background: 'var(--warning-soft)', color: 'var(--warning-ink)', borderRadius: 3, padding: '0 3px', fontFamily: 'var(--font-mono)', fontSize: 12 },
  link: { display: 'inline-flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', padding: 0, color: 'var(--link)', cursor: 'pointer', font: 'inherit', fontSize: 12.5 },
  aiView: { marginTop: 8 },
  aiLabel: { fontSize: 11, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--ink-faint)', margin: '6px 0 4px' },
  pre: { margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.5, background: 'var(--surface-2)', borderRadius: 6, padding: 8, maxHeight: 320, overflow: 'auto' },
};
