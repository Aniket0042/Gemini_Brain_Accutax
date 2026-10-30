import React, { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Bot,
  Calendar,
  CheckCircle2,
  Clock,
  Layers,
  Loader2,
  Send,
  Sparkles,
  Wand2,
  X,
} from 'lucide-react';
import SetupCard from './SetupCard';
import TryResult from './TryResult';
import BotIntro from './BotIntro';
import {
  checkBotDraft,
  createBotOnServer,
  createDraftFromChat,
  createDraftFromSentence,
  createDraftFromTemplate,
  fetchBotTemplates,
  saveBot,
  tryBotDraft,
  updateBotOnServer,
} from '../../services/bots';
import './bots.css';

// One click fills the sentence box; the user can edit it before building.
const SENTENCE_EXAMPLES = [
  'Every Monday at 9am, check my bills for duplicates',
  'Remind me 7 days before each VAT return',
  'Check sales invoices for wrong VAT on the 1st of each month',
];

const TEMPLATE_ICONS = {
  vat_weekly_digest: Calendar,
  vat_return_reminder: Clock,
  vat_full_watch: Layers,
};

export default function BotBuilder({
  open,
  onClose,
  token = '',
  orgIds = [],
  orgNames = {},
  start = { mode: 'blank' },
  onSeeFindingsInChat,
  onSeeFindings,
  onSaved,
}) {
  const [step, setStep] = useState('start'); // 'start' | 'setup' | 'intro'
  const [sentenceText, setSentenceText] = useState('');
  const [templates, setTemplates] = useState([]);
  const [draft, setDraft] = useState(null);
  const [issues, setIssues] = useState([]);
  const [nextRuns, setNextRuns] = useState([]);
  const [credits, setCredits] = useState(0);
  const [tryResult, setTryResult] = useState(null);
  const [createdBot, setCreatedBot] = useState(null);
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  const debounceTimerRef = useRef(null);

  // Load templates on open
  useEffect(() => {
    if (!open) {
      setStep('start');
      setDraft(null);
      setTryResult(null);
      setCreatedBot(null);
      setErrorMsg('');
      setSentenceText('');
      return;
    }

    fetchBotTemplates('vat', token)
      .then((res) => setTemplates(res.templates || []))
      .catch((err) => console.warn('Could not load bot templates:', err));

    // Editing a saved bot: open its settings on the setup card.
    if (start?.mode === 'edit' && start.initialDraft) {
      setDraft(start.initialDraft);
      setStep('setup');
      checkBotDraft(start.initialDraft, token)
        .then((res) => {
          setIssues(res.issues || []);
          setNextRuns(res.next_runs || []);
          setCredits(res.monthly_credits || 0);
        })
        .catch(() => null);
      return;
    }

    // Started from a template card on the Bots home.
    if (start?.mode === 'template' && start.templateId) {
      handlePickTemplate(start.templateId);
      return;
    }

    // If starting from chat action
    if (start?.mode === 'chat' && start.question) {
      setBusy(true);
      createDraftFromChat(start.question, 'vat', orgIds, token)
        .then((res) => {
          setDraft(res.draft);
          setIssues(res.issues || []);
          setNextRuns(res.next_runs || []);
          setCredits(res.monthly_credits || 0);
          setStep('setup');
        })
        .catch((err) => setErrorMsg(err.message || 'Could not build bot from chat'))
        .finally(() => setBusy(false));
    }
  }, [open, start, token]);

  if (!open) return null;

  // Template select
  const handlePickTemplate = async (templateId) => {
    setBusy(true);
    setErrorMsg('');
    try {
      const res = await createDraftFromTemplate(templateId, orgIds, token);
      setDraft(res.draft);
      setIssues(res.issues || []);
      setNextRuns(res.next_runs || []);
      setCredits(res.monthly_credits || 0);
      setStep('setup');
    } catch (err) {
      setErrorMsg(err.message || 'Failed to initialize template');
    } finally {
      setBusy(false);
    }
  };

  // Sentence submit
  const handleSentenceSubmit = async (e) => {
    e?.preventDefault();
    if (!sentenceText.trim()) return;
    setBusy(true);
    setErrorMsg('');
    try {
      const res = await createDraftFromSentence(sentenceText.trim(), orgIds, token);
      setDraft(res.draft);
      setIssues(res.issues || []);
      setNextRuns(res.next_runs || []);
      setCredits(res.monthly_credits || 0);
      setStep('setup');
    } catch (err) {
      setErrorMsg(err.message || 'Could not interpret sentence');
    } finally {
      setBusy(false);
    }
  };

  // Draft change with 400ms debounce check
  const handleDraftChange = (newDraft) => {
    setDraft(newDraft);
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(async () => {
      try {
        const res = await checkBotDraft(newDraft, token);
        setIssues(res.issues || []);
        setNextRuns(res.next_runs || []);
        setCredits(res.monthly_credits || 0);
      } catch (err) {
        console.warn('Draft validation check error:', err);
      }
    }, 400);
  };

  // Try on last month
  const handleTry = async () => {
    setBusy(true);
    setErrorMsg('');
    try {
      const res = await tryBotDraft(draft, 'last_month', token);
      setTryResult(res);
    } catch (err) {
      setErrorMsg(err.message || 'Trial run failed');
    } finally {
      setBusy(false);
    }
  };

  // Turn on bot
  const handleTurnOn = async () => {
    setBusy(true);
    setErrorMsg('');
    try {
      // Editing: save the changes to the same bot and close.
      if (start?.mode === 'edit' && start.botId) {
        await updateBotOnServer(start.botId, { draft }, token);
        onSaved?.(start.botId);
        onClose();
        return;
      }

      // 1. Save to the account. Without a sign-in (local demo) it is kept in this browser; with one, a failure is
      //    shown rather than hidden in the browser, so the user never ends up with a bot that only seems saved.
      const bot = token ? await createBotOnServer(draft, 'active', token) : saveBot(draft);
      setCreatedBot(bot);
      onSaved?.(bot?.id);

      // 2. First run trial
      const res = await tryBotDraft(draft, 'this_quarter', token);
      setTryResult(res);

      // 3. Show intro step
      setStep('intro');
    } catch (err) {
      setErrorMsg(err.message || 'Failed to activate bot');
    } finally {
      setBusy(false);
    }
  };

  const handleSeeFindings = () => {
    onClose();
    if (onSeeFindingsInChat) {
      onSeeFindingsInChat();
    } else if (onSeeFindings) {
      onSeeFindings();
    }
  };

  return (
    <div className="bot-modal-backdrop" onClick={onClose}>
      <div
        className="bot-modal-container"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="bot-modal-top">
          <div className="bot-modal-title">
            <span className="bot-modal-title-icon"><Bot className="w-4 h-4" /></span>
            <span>
              {step === 'start'
                ? 'Create a VAT bot'
                : step === 'setup'
                ? (start?.mode === 'edit' ? 'Edit your VAT bot' : 'Set up your VAT bot')
                : 'Your bot is on'}
            </span>
          </div>
          <button
            type="button"
            className="btn-icon-subtle"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="w-4 h-4 text-ink-faint" />
          </button>
        </div>

        {errorMsg && (
          <div className="bot-modal-error">
            <AlertTriangle className="w-4 h-4 text-danger flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="bot-modal-content">
          {/* ── STEP 1: START SCREEN (Sentence or Templates) ───────────── */}
          {step === 'start' && (
            <div className="bot-start-view">
              <div className="bot-sentence-box">
                <label className="bot-sentence-label" htmlFor="bot-sentence-input">
                  <Wand2 className="w-4 h-4 text-accent" />
                  <span>Describe what you want your bot to do</span>
                </label>
                <p className="bot-sentence-hint">
                  Say when it runs and what it checks. You can change everything on the next screen.
                </p>
                <form onSubmit={handleSentenceSubmit} className="bot-sentence-form">
                  <input
                    id="bot-sentence-input"
                    type="text"
                    className="input-text-clean"
                    placeholder="Every Monday at 9am, check my bills for duplicates"
                    value={sentenceText}
                    onChange={(e) => setSentenceText(e.target.value)}
                    disabled={busy}
                    maxLength={500}
                    autoFocus
                  />
                  <button
                    type="submit"
                    className="btn-primary"
                    disabled={busy || !sentenceText.trim()}
                  >
                    {busy ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    <span>Build bot</span>
                  </button>
                </form>
                <div className="bot-sentence-examples">
                  {SENTENCE_EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      className="bot-sentence-example"
                      onClick={() => setSentenceText(ex)}
                      disabled={busy}
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>

              <div className="bot-or-divider">
                <span>or start from a template</span>
              </div>

              <div className="bot-templates-grid">
                {templates.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="bot-template-card"
                    onClick={() => handlePickTemplate(t.id)}
                    disabled={busy}
                  >
                    <div className="bot-template-top">
                      <span className="bot-template-icon">
                        {(() => {
                          const Icon = TEMPLATE_ICONS[t.id] || Sparkles;
                          return <Icon className="w-4 h-4" />;
                        })()}
                      </span>
                      <h4 className="bot-template-title">{t.title}</h4>
                    </div>
                    <p className="bot-template-desc">{t.summary}</p>
                    <span className="bot-template-cta">Use this template →</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── STEP 2: SETUP CARD ────────────────────────────────────── */}
          {step === 'setup' && (
            <div className="bot-setup-view">
              <SetupCard
                draft={draft}
                issues={issues}
                nextRuns={nextRuns}
                credits={credits}
                onChange={handleDraftChange}
                onTry={handleTry}
                onTurnOn={handleTurnOn}
                turnOnLabel={start?.mode === 'edit' ? 'Save changes' : 'Turn on'}
                busy={busy}
              />

              {tryResult && (
                <div className="mt-6">
                  <TryResult result={tryResult} />
                </div>
              )}
            </div>
          )}

          {/* ── STEP 3: BOT INTRO ─────────────────────────────────────── */}
          {step === 'intro' && (
            <div className="bot-intro-view">
              <BotIntro
                bot={createdBot}
                result={tryResult}
                onSeeFindings={handleSeeFindings}
                onAdjust={() => setStep('setup')}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
