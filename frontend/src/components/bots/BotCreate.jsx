import React, { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Bot, Building2, Calendar, Check, Clock, Layers, Loader2, Sparkles, Wand2 } from 'lucide-react';
import { createDraftFromSentence, createDraftFromTemplate, fetchBotTemplates } from '../../services/bots';
import { credits, DELIVER_LABEL, scheduleChip, TEMPLATE_META, WATCH_INFO } from './botText';
import './bots.css';
import './bots-pages.css';
import './bots-builder.css';

const MAX_TEXT = 500;

const EXAMPLES = [
  'Every Monday at 9am, check my bills for duplicates',
  'Remind me 7 days before each VAT return',
  'Check sales invoices for wrong VAT on the 1st of each month',
];

const TEMPLATE_ICONS = { vat_weekly_digest: Calendar, vat_return_reminder: Clock, vat_full_watch: Layers };

const isOrgQuestion = (q) => /organi[sz]ation/i.test(q);

/**
 * Create a bot: describe it in one sentence (the server turns it into a draft and says what it understood), or
 * start from a template. Either way ends with `onDraft(response)`, which opens the builder.
 * `orgs`: [{id, name}] the user can pick from; `orgIds`: the organizations selected in the app now.
 */
export default function BotCreate({ token = '', orgs = [], orgIds = [], orgLabel = '', notice = '', onBack, onDraft }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [templates, setTemplates] = useState([]);

  useEffect(() => {
    fetchBotTemplates('vat', token).then((r) => setTemplates(r.templates || [])).catch(() => setTemplates([]));
  }, [token]);

  const build = async (e) => {
    e?.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      setResult(await createDraftFromSentence(text.trim(), orgIds, token));
    } catch (err) {
      setError(err.message || 'Could not read that. Try saying when it runs and what it checks.');
    } finally {
      setBusy(false);
    }
  };

  const useTemplate = async (id) => {
    setBusy(true);
    setError('');
    try {
      onDraft(await createDraftFromTemplate(id, orgIds, token));
    } catch (err) {
      setError(err.message || 'Could not start from that template.');
      setBusy(false);
    }
  };

  // Answering "Which organization is …?": pick one, and the question goes away.
  const pickOrg = (id) => {
    setResult((r) => ({
      ...r,
      draft: { ...r.draft, org_ids: [id], questions: (r.draft.questions || []).filter((q) => !isOrgQuestion(q)) },
    }));
  };

  const draft = result?.draft;
  const questions = draft?.questions || [];
  const watchLabels = (draft?.watches || []).map((w) => WATCH_INFO.find((x) => x.id === w)?.label).filter(Boolean);

  return (
    <div className="bots-page bc-page">
      <div className="bc-top">
        <button type="button" className="bh-btn" onClick={onBack}><ArrowLeft size={14} /> Bots</button>
        <span className="bc-top-title">New bot</span>
        {orgLabel && <span className="bh-pill bc-org"><Building2 size={12} /> {orgLabel}</span>}
      </div>

      <div className="bc-hero">
        <span className="bc-hero-icon"><Bot size={20} /></span>
        <h1>What should your bot keep an eye on?</h1>
        <p>Say when it runs, what it checks and how to tell you. We'll fill in the rest, and you can change anything on the next screen.</p>
      </div>

      <form className="bc-box" onSubmit={build}>
        <label htmlFor="bc-text" className="sr-only">Describe your bot</label>
        <textarea
          id="bc-text"
          className="bc-textarea"
          value={text}
          maxLength={MAX_TEXT}
          rows={3}
          placeholder="Every Monday at 9am check my bills for duplicates and wrong VAT, and alert me"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) build(e); }}
          disabled={busy}
          autoFocus
        />
        <div className="bc-box-foot">
          <span className="bd-muted">{text.length} / {MAX_TEXT} · Names and numbers are masked before the AI reads them</span>
          <button type="submit" className="bh-btn is-primary" disabled={busy || !text.trim()}>
            {busy ? <Loader2 size={14} className="bd-spin" /> : <Wand2 size={14} />} Build bot
          </button>
        </div>
      </form>

      {(error || notice) && <p className="bd-alert bc-narrow">{error || notice}</p>}

      {draft && (
        <section className="bc-understood">
          <h2><Check size={15} /> Here's what we understood</h2>
          <div className="bc-chips">
            {(draft.schedules || []).map((s, i) => <span key={`s${i}`} className="bc-chip"><Clock size={12} /> {scheduleChip(s)}</span>)}
            {(draft.schedules || []).length === 0 && <span className="bc-chip is-missing">No schedule yet</span>}
            {watchLabels.map((l) => <span key={l} className="bc-chip">{l}</span>)}
            <span className="bc-chip">{(draft.deliver || ['inbox']).map((d) => DELIVER_LABEL[d] || d).join(' + ')}</span>
          </div>
          {questions.map((q) => (
            <div key={q} className="bc-question">
              <p>{q}</p>
              {isOrgQuestion(q) && orgs.length === 0 && orgIds.length === 0 && (
                <span className="bd-muted">You can choose the organization on the next screen.</span>
              )}
              {isOrgQuestion(q) && (orgs.length > 0 || orgIds.length > 0) && (
                <div className="bc-chips">
                  {orgs.slice(0, 6).map((o) => (
                    <button key={o.id} type="button" className="bc-chip is-choice" onClick={() => pickOrg(o.id)}>{o.name}</button>
                  ))}
                  {orgIds.length > 0 && (
                    <button type="button" className="bc-chip is-choice" onClick={() => pickOrg(orgIds[0])}>Use my current organization</button>
                  )}
                </div>
              )}
            </div>
          ))}
          <div className="bc-understood-foot">
            {questions.some((q) => !isOrgQuestion(q)) && <span className="bd-muted">You can answer the rest on the next screen.</span>}
            <button type="button" className="bh-btn is-primary" onClick={() => onDraft(result)}>
              Review and turn on <ArrowRight size={14} />
            </button>
          </div>
        </section>
      )}

      <div className="bc-examples">
        <span className="bd-muted">Try one of these</span>
        <div className="bc-chips is-center">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="bc-chip is-choice" onClick={() => { setText(ex); setResult(null); }}>{ex}</button>
          ))}
        </div>
      </div>

      <div className="bot-or-divider bc-divider"><span>or start from a template</span></div>

      <div className="bc-templates">
        {templates.map((t) => {
          const Icon = TEMPLATE_ICONS[t.id] || Sparkles;
          const meta = TEMPLATE_META[t.id] || {};
          return (
            <div key={t.id} className="bc-template">
              <div className="bc-template-head">
                <span className="bh-template-icon"><Icon size={16} /></span>
                <div>
                  <h3>{t.title}</h3>
                  {meta.tag && <span className="bc-template-tag">{meta.tag}</span>}
                </div>
              </div>
              <p className="bh-template-sub">{t.summary}</p>
              <dl className="bc-template-facts">
                <dt>Runs</dt><dd>{meta.runs || ''}</dd>
                <dt>Checks</dt><dd>{(t.draft?.watches || []).length} of {WATCH_INFO.length}</dd>
                <dt>Cost</dt><dd>{t.monthly_credits != null ? `≈ ${credits(t.monthly_credits)} credits / mo` : ''}</dd>
              </dl>
              <button type="button" className="bh-btn bc-template-btn" onClick={() => useTemplate(t.id)} disabled={busy}>
                Use this template
              </button>
            </div>
          );
        })}
      </div>

      <p className="bd-muted bc-tip">Tip: under any VAT answer in chat, press <strong>Make this a bot</strong> to repeat it on a schedule.</p>
    </div>
  );
}
