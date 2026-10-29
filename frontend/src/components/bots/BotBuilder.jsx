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
} from '../../services/bots';

export default function BotBuilder({
  open,
  onClose,
  token = '',
  orgIds = [],
  orgNames = {},
  start = { mode: 'blank' },
  onSeeFindingsInChat,
  onSeeFindings,
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
      // 1. Save in server store (or fallback to browser store)
      let bot = null;
      if (token) {
        try {
          bot = await createBotOnServer(draft, 'active', token);
        } catch {
          bot = saveBot(draft);
        }
      } else {
        bot = saveBot(draft);
      }
      setCreatedBot(bot);

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
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-accent" />
            <span className="font-semibold text-ink">
              {step === 'start'
                ? 'Create a VAT Bot'
                : step === 'setup'
                ? 'Configure VAT Bot'
                : 'Bot Activated'}
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
                <label className="bot-sentence-label">
                  <Wand2 className="w-4 h-4 text-accent" />
                  <span>Describe what you want your bot to do</span>
                </label>
                <form onSubmit={handleSentenceSubmit} className="flex gap-2 mt-2">
                  <input
                    type="text"
                    className="input-text-clean flex-1"
                    placeholder="e.g. Check for duplicate bills every Monday at 9am and email me..."
                    value={sentenceText}
                    onChange={(e) => setSentenceText(e.target.value)}
                    disabled={busy}
                    autoFocus
                  />
                  <button
                    type="submit"
                    className="btn-primary flex items-center gap-2"
                    disabled={busy || !sentenceText.trim()}
                  >
                    {busy ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    <span>Build</span>
                  </button>
                </form>
              </div>

              <div className="bot-or-divider">
                <span>or pick a ready-made template</span>
              </div>

              <div className="bot-templates-grid">
                {templates.map((t) => (
                  <div
                    key={t.id}
                    className="bot-template-card"
                    onClick={() => !busy && handlePickTemplate(t.id)}
                  >
                    <div className="flex items-start justify-between">
                      <h4 className="bot-template-title">{t.title}</h4>
                      <Sparkles className="w-4 h-4 text-accent flex-shrink-0" />
                    </div>
                    <p className="bot-template-desc">{t.summary}</p>
                    <div className="bot-template-footer">
                      <span className="text-xs text-accent font-medium">
                        Use this template →
                      </span>
                    </div>
                  </div>
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
