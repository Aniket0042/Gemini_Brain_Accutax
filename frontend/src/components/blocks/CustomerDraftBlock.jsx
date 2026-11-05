import React, { useEffect, useState } from 'react';
import { AlertTriangle, Check, Loader2, UserPlus, X } from 'lucide-react';
import { discardDraft, fetchDraft, saveDraft } from '../../services/salesDrafts';

/**
 * CustomerDraftBlock — a new customer the assistant prepared (backend invoicing/customer.py). It is created in
 * Accutax only when the person clicks Create customer; the server then creates it once, as them. A card in a
 * reopened chat refreshes its state on mount.
 */
export function CustomerDraftBlock({ block, token }) {
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
  const label = { confirming: 'Creating…', done: 'Customer created', failed: 'Not confirmed', discarded: 'Withdrawn', expired: 'Expired' }[status];

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
    <section className={`sd-card sd-status-${status}`} aria-label="New customer">
      <header className="sd-head">
        <h3 className="sd-title"><UserPlus size={15} aria-hidden="true" />New customer</h3>
        {label && <span className={`sd-badge sd-badge-${status}`}>{status === 'done' && <Check size={12} aria-hidden="true" />}{label}</span>}
      </header>
      <dl className="sd-meta">
        {v.organization?.name && <div><dt>Organization</dt><dd>{v.organization.name}</dd></div>}
        <div><dt>Name</dt><dd>{v.name}</dd></div>
        <div><dt>Email</dt><dd>{v.email}</dd></div>
        <div><dt>TRN</dt><dd>{v.trn || '—'}</dd></div>
        {v.phone && <div><dt>Phone</dt><dd>{v.phone}</dd></div>}
        <div><dt>Currency</dt><dd>{v.currency}</dd></div>
      </dl>
      {open && (v.warnings || []).map((w, i) => (
        <p key={i} className="sd-warning"><AlertTriangle size={14} aria-hidden="true" />{w}</p>
      ))}
      {(error || (lastError && status !== 'done')) && <p className="sd-error" role="alert">{error || lastError}</p>}
      {open && (
        <div className="report-actions sd-actions">
          <button type="button" className="report-action sd-primary" disabled={busy}
            onClick={() => run(() => saveDraft(card.action_id, card.version, token))}>
            {busy ? <Loader2 size={14} className="spin" aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}
            Create customer
          </button>
          <button type="button" className="report-action" disabled={busy}
            onClick={() => run(() => discardDraft(card.action_id, token))}>
            <X size={14} aria-hidden="true" />Discard
          </button>
        </div>
      )}
      {open && <p className="sd-faint sd-foot">The customer is not created in Accutax until you click Create customer.</p>}
    </section>
  );
}
