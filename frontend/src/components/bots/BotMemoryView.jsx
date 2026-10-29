import React, { useState, useEffect } from 'react';
import {
  Brain,
  ArrowLeft,
  Trash2,
  Plus,
  Sparkles,
  RefreshCw,
  AlertCircle,
  Info,
  Check,
} from 'lucide-react';
import { fetchBotMemory, addBotMemory, deleteBotMemory } from '../../services/bots';

export default function BotMemoryView({
  bot,
  onBack,
  onMemoryChanged,
  token = '',
}) {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState(null);
  const [newNote, setNewNote] = useState('');
  const [addingNote, setAddingNote] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const botName = bot?.draft?.name || bot?.name || 'VAT Watch Bot';
  const botId = bot?.id;

  const loadMemory = async () => {
    if (!botId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchBotMemory(botId, token);
      setMemories(data || []);
    } catch (err) {
      console.error('Failed to load bot memory:', err);
      setError('Could not load memory items for this bot.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMemory();
  }, [botId]);

  const handleAddNote = async (e) => {
    e?.preventDefault();
    if (!newNote.trim() || addingNote) return;

    setAddingNote(true);
    setError(null);
    setNotice(null);
    try {
      await addBotMemory(botId, newNote.trim(), 'note', token);
      setNewNote('');
      setNotice('New instruction saved. The bot will follow this rule in future runs.');
      await loadMemory();
      onMemoryChanged?.();
    } catch (err) {
      console.error('Failed to add instruction:', err);
      setError(err.message || 'Failed to add instruction');
    } finally {
      setAddingNote(false);
    }
  };

  const handleDelete = async (item) => {
    if (deletingId) return;
    setDeletingId(item.id);
    setError(null);
    setNotice(null);
    try {
      await deleteBotMemory(botId, item.id, token);
      if (item.finding_key || item.source === 'dismissal') {
        setNotice('Memory line removed. The associated finding has been restored to your inbox.');
      } else {
        setNotice('Instruction forgotten.');
      }
      await loadMemory();
      onMemoryChanged?.();
    } catch (err) {
      console.error('Failed to delete memory:', err);
      setError(err.message || 'Failed to remove memory line');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="bot-memory-container">
      {/* Header */}
      <div className="bot-memory-header">
        <div className="flex items-center gap-2">
          {onBack && (
            <button
              type="button"
              className="btn-icon-subtle"
              onClick={onBack}
              title="Back to bots"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
          )}
          <div>
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-accent" />
              <h3 className="bot-list-title" style={{ margin: 0 }}>
                {botName} &mdash; Memory
              </h3>
            </div>
            <p className="bot-list-subtitle" style={{ margin: '2px 0 0 0' }}>
              What this bot learned from your dismissals and custom instructions
            </p>
          </div>
        </div>
        <button
          type="button"
          className="btn-icon-subtle"
          onClick={loadMemory}
          disabled={loading}
          title="Refresh memory"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Notice / Feedback */}
      {notice && (
        <div className="bot-alert success flex items-center gap-2 py-2 px-3 text-xs mb-2">
          <Check className="w-3.5 h-3.5 text-accent flex-shrink-0" />
          <span>{notice}</span>
        </div>
      )}
      {error && (
        <div className="bot-alert error flex items-center gap-2 py-2 px-3 text-xs mb-2">
          <AlertCircle className="w-3.5 h-3.5 text-danger flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Info Tip */}
      <div className="bot-hint-box flex items-start gap-2 py-2 px-3 mb-3 bg-surface-2 rounded-md border border-border">
        <Info className="w-4 h-4 text-accent flex-shrink-0 mt-0.5" />
        <div className="text-xs text-ink-muted">
          <span>
            When you dismiss a problem in the inbox with &ldquo;Not a problem&rdquo;, the bot remembers the rule and supplier so it won&apos;t alert you again.
            Removing a dismissed memory line automatically restores that finding to your inbox.
          </span>
        </div>
      </div>

      {/* Teach Bot / Add Instruction input */}
      <form onSubmit={handleAddNote} className="flex items-center gap-2 mb-4">
        <input
          type="text"
          className="input-field flex-1 text-sm py-1.5 px-3"
          placeholder="Teach this bot a rule (e.g., 'Al Naboodah bills always include 5% VAT')..."
          value={newNote}
          onChange={(e) => setNewNote(e.target.value)}
          disabled={addingNote}
        />
        <button
          type="submit"
          className="btn-primary text-xs py-2 px-3.5 flex items-center gap-1.5 flex-shrink-0"
          disabled={!newNote.trim() || addingNote}
        >
          <Plus className="w-3.5 h-3.5" />
          <span>{addingNote ? 'Saving…' : 'Teach Bot'}</span>
        </button>
      </form>

      {/* Memory List */}
      {loading ? (
        <div className="text-center py-8 text-sm text-ink-faint">
          <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2 text-accent" />
          <span>Loading learned rules…</span>
        </div>
      ) : memories.length === 0 ? (
        <div className="text-center py-8 px-4 bg-surface rounded-lg border border-border">
          <Sparkles className="w-6 h-6 mx-auto mb-2 text-ink-faint" />
          <h4 className="text-sm font-semibold text-ink">No memory lines yet</h4>
          <p className="text-xs text-ink-muted max-w-sm mx-auto mt-1">
            This bot hasn&apos;t learned any custom rules yet. Dismiss findings in your inbox with &ldquo;Not a problem&rdquo; or add instructions above to teach it.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {memories.map((m) => {
            const isDismissal = m.source === 'dismissal';
            const isBusy = deletingId === m.id;
            const dateStr = m.created_at ? new Date(m.created_at).toLocaleDateString(undefined, {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            }) : null;

            return (
              <div key={m.id} className="bot-memory-card">
                <div className="flex items-center gap-3 min-w-0 pr-2">
                  <span className={`bot-memory-tag ${isDismissal ? 'dismissal' : 'note'}`}>
                    {isDismissal ? 'Dismissal' : 'Note'}
                  </span>
                  <div className="min-w-0">
                    <div className="bot-memory-text break-words">{m.text}</div>
                    {dateStr && (
                      <span className="text-2xs text-ink-faint mt-0.5 block">
                        Learned on {dateStr}
                      </span>
                    )}
                  </div>
                </div>

                <button
                  type="button"
                  className="btn-danger-subtle text-xs py-1 px-2.5 flex items-center gap-1 flex-shrink-0"
                  onClick={() => handleDelete(m)}
                  disabled={isBusy}
                  title={isDismissal ? 'Forget rule and un-dismiss finding' : 'Forget instruction'}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>{isBusy ? 'Removing…' : 'Forget'}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
