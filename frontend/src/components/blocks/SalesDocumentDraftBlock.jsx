import React, { useEffect, useState } from 'react';
import { AlertTriangle, Check, ExternalLink, FileText, Loader2, X } from 'lucide-react';
import { formatDay } from './VatReviewBlock';
import { discardDraft, fetchDraft, saveDraft } from '../../services/salesDrafts';

/**
 * SalesDocumentDraftBlock — a sales invoice or quote the assistant prepared (backend invoicing/blocks.py).
 * Everything shown comes from the server's card, never from the model's text. Nothing is written to Accutax
 * until the person clicks Save as draft; the server then saves it once, as them.
 *
 * A card in a reopened chat refreshes its state on mount, so it never offers Save for a draft that was already
 * saved, withdrawn or expired.
 */
const STATUS_TEXT = {
  pending: null,
  confirming: 'Saving…',
  done: 'Saved as a draft in Accutax',
  doneUpdate: 'Changes saved in Accutax',
  failed: 'Not confirmed',
  discarded: 'Withdrawn',
  expired: 'Expired',
};

export function SalesDocumentDraftBlock({ block, token }) {
  const [card, setCard] = useState(block);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    if (!block?.action_id) return undefined;
    fetchDraft(block.action_id, token)
      .then((data) => { if (live && data?.block) setCard(data.block); })
      .catch((e) => { if (live && e.status === 404) setCard((c) => ({ ...c, status: 'expired' })); });
    return () => { live = false; };
  }, [block?.action_id, token]);

  if (!card?.view) return null;
  const v = card.view;
  const status = card.status || 'pending';
  const open = status === 'pending';

  const run = async (fn) => {
    setBusy(true);
    setError('');
    try {
      const data = await fn();
      if (data?.block) setCard(data.block);
    } catch (e) {
      setError(e.message);
      if (e.status === 409 || e.status === 410) fetchDraft(card.action_id, token).then((d) => d?.block && setCard(d.block)).catch(() => {});
    } finally {
      setBusy(false);
    }
  };

  const isUpdate = v.mode === 'update';
  const statusText = status === 'done' && isUpdate ? STATUS_TEXT.doneUpdate : STATUS_TEXT[status];
  const lastError = card.error?.message;

  return (
    <section className={`sd-card sd-status-${status}`} aria-label={`${v.document_label} draft`}>
      <header className="sd-head">
        <h3 className="sd-title"><FileText size={15} aria-hidden="true" />{isUpdate ? `Change to ${v.document_label.toLowerCase()} ${v.number}` : `${v.document_label} draft`}</h3>
        {v.number && !isUpdate && <span className="sd-sub">{status === 'done' ? '' : 'Suggested no. '}{card.result?.number || v.number}</span>}
        {statusText && <span className={`sd-badge sd-badge-${status}`}>{status === 'done' && <Check size={12} aria-hidden="true" />}{statusText}</span>}
      </header>

      {v.copied_from && (
        <p className="sd-faint sd-copied">
          Copied from {v.copied_from.type.toLowerCase()} {v.copied_from.number}
          {v.copied_from.linked ? ' · the quote is marked as converted when you save' : ''}
        </p>
      )}
      {isUpdate && open && (
        <p className="sd-faint sd-copied">This replaces the lines and details of the draft you saved. It stays unchanged until you click Save changes.</p>
      )}

      <dl className="sd-meta">
        <div><dt>Customer</dt><dd>{v.customer?.name}{v.customer?.trn ? <span className="sd-faint"> · TRN {v.customer.trn}</span> : null}</dd></div>
        <div><dt>Date</dt><dd>{formatDay(v.date)}</dd></div>
        <div><dt>{v.due_label || 'Due'}</dt><dd>{formatDay(v.due_date)}</dd></div>
        <div><dt>Currency</dt><dd>{v.currency}</dd></div>
      </dl>

      <table className="sd-lines">
        <thead>
          <tr><th>Item</th><th className="sd-num">Qty</th><th className="sd-num">Price</th><th>VAT</th><th className="sd-num">Amount</th></tr>
        </thead>
        <tbody>
          {(v.lines || []).map((l, i) => (
            <tr key={i}>
              <td>{l.description}{l.discount ? <span className="sd-faint"> · less {l.discount}</span> : null}</td>
              <td className="sd-num">{l.quantity}</td>
              <td className="sd-num">{l.unit_price}</td>
              <td>{l.vat}</td>
              <td className="sd-num">{l.line_total}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="sd-totals">
        <div><dt>Subtotal</dt><dd>{v.subtotal}</dd></div>
        <div><dt>VAT</dt><dd>{v.vat}</dd></div>
        <div className="sd-total"><dt>Total</dt><dd>{v.currency} {v.total}</dd></div>
      </dl>

      {v.notes && <p className="sd-notes">{v.notes}</p>}
      {open && (v.warnings || []).map((w, i) => (
        <p key={i} className="sd-warning"><AlertTriangle size={14} aria-hidden="true" />{w}</p>
      ))}
      {(error || (lastError && status !== 'done')) && <p className="sd-error" role="alert">{error || lastError}</p>}

      <div className="report-actions sd-actions">
        {open && (
          <>
            <button type="button" className="report-action sd-primary" disabled={busy}
              onClick={() => run(() => saveDraft(card.action_id, card.version, token))}>
              {busy ? <Loader2 size={14} className="spin" aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}
              {isUpdate ? 'Save changes' : 'Save as draft'}
            </button>
            <button type="button" className="report-action" disabled={busy}
              onClick={() => run(() => discardDraft(card.action_id, token))}>
              <X size={14} aria-hidden="true" />Discard
            </button>
          </>
        )}
        {status === 'done' && card.result?.url && (
          <button type="button" className="report-action"
            onClick={() => window.open(card.result.url, '_blank', 'noopener,noreferrer')}>
            <ExternalLink size={14} aria-hidden="true" />Open in Accutax
          </button>
        )}
      </div>
      {open && <p className="sd-faint sd-foot">{isUpdate ? 'Nothing changes in Accutax until you click Save changes.' : 'Nothing is saved in Accutax until you click Save as draft.'}</p>}
    </section>
  );
}
