import React, { useEffect, useState } from 'react';
import { AlertTriangle, BookCheck, Check, ExternalLink, Loader2, X } from 'lucide-react';
import { formatDay } from './VatReviewBlock';
import { discardDraft, fetchDraft, saveDraft } from '../../services/salesDrafts';

/**
 * FinaliseDraftBlock — the finalising of a draft invoice the assistant prepared (backend invoicing/finalise.py).
 * Finalising posts the invoice to the books. It happens only when the person clicks Finalise and post; the server
 * then does it once, as them, after checking the invoice again. A card in a reopened chat refreshes on mount.
 */
export function FinaliseDraftBlock({ block, token }) {
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
  const lastError = card.error?.message;
  const label = { confirming: 'Posting…', done: 'Posted to your books', failed: 'Not confirmed', discarded: 'Withdrawn', expired: 'Expired' }[status];

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

  return (
    <section className={`sd-card sd-status-${status}`} aria-label="Finalise invoice">
      <header className="sd-head">
        <h3 className="sd-title"><BookCheck size={15} aria-hidden="true" />Finalise invoice {v.number}</h3>
        {label && <span className={`sd-badge sd-badge-${status}`}>{status === 'done' && <Check size={12} aria-hidden="true" />}{label}</span>}
      </header>
      <dl className="sd-meta">
        <div><dt>Customer</dt><dd>{v.customer || '—'}</dd></div>
        <div><dt>Date</dt><dd>{formatDay(v.date)}</dd></div>
        <div><dt>Due</dt><dd>{formatDay(v.due_date)}</dd></div>
        <div><dt>Lines</dt><dd>{v.lines}</dd></div>
      </dl>
      <dl className="sd-totals">
        <div><dt>Subtotal</dt><dd>{v.subtotal}</dd></div>
        <div><dt>VAT</dt><dd>{v.vat}</dd></div>
        <div className="sd-total"><dt>Total</dt><dd>{v.currency} {v.total}</dd></div>
      </dl>
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
              Finalise and post
            </button>
            <button type="button" className="report-action" disabled={busy}
              onClick={() => run(() => discardDraft(card.action_id, token))}>
              <X size={14} aria-hidden="true" />Cancel
            </button>
          </>
        )}
        {(status === 'done' || status === 'failed') && card.result?.url && (
          <button type="button" className="report-action"
            onClick={() => window.open(card.result.url, '_blank', 'noopener,noreferrer')}>
            <ExternalLink size={14} aria-hidden="true" />Open in Accutax
          </button>
        )}
      </div>
      {open && <p className="sd-faint sd-foot">Nothing is posted to your books until you click Finalise and post.</p>}
    </section>
  );
}
