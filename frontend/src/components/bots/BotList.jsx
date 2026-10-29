import React from 'react';
import {
  Bot,
  Brain,
  Calendar,
  Clock,
  Edit2,
  Pause,
  Play,
  Plus,
  Power,
  Trash2,
} from 'lucide-react';

export default function BotList({
  bots = [],
  onEdit,
  onToggle,
  onDelete,
  onNewBot,
  onViewMemory,
}) {
  return (
    <div className="bot-list-container">
      <div className="bot-list-header">
        <div>
          <h3 className="bot-list-title">Your Bots</h3>
          <p className="bot-list-subtitle">
            Autonomous assistants watching your books and deadlines
          </p>
        </div>
        {onNewBot && (
          <button
            type="button"
            className="btn-primary flex items-center gap-2"
            onClick={onNewBot}
          >
            <Plus className="w-4 h-4" />
            <span>New bot</span>
          </button>
        )}
      </div>

      {bots.length === 0 ? (
        <div className="bot-list-empty">
          <div className="bot-empty-avatar">
            <Bot className="w-8 h-8 text-ink-faint" />
          </div>
          <h4>No bots configured</h4>
          <p>
            Create a bot to automatically review bills for duplicate payments,
            detect VAT errors, and remind you of return deadlines.
          </p>
          {onNewBot && (
            <button
              type="button"
              className="btn-secondary flex items-center gap-2 mt-4"
              onClick={onNewBot}
            >
              <Plus className="w-4 h-4" />
              <span>Create your first bot</span>
            </button>
          )}
        </div>
      ) : (
        <div className="bot-cards-grid">
          {bots.map((b) => {
            const { id, draft, status, createdAt } = b;
            const isActive = status === 'active';
            const schedules = draft?.schedules || [];
            const schedText = schedules.length
              ? schedules
                  .map((s) => (s.kind === 'weekly' ? `Weekly (${s.day || 'Mon'})` : s.kind === 'before_deadline' ? 'Before deadline' : s.kind))
                  .join(' · ')
              : 'No schedule';

            return (
              <div key={id} className={`bot-item-card ${isActive ? 'active' : 'paused'}`}>
                <div className="bot-item-top">
                  <div className="flex items-center gap-3">
                    <div className="bot-item-avatar">
                      <Bot className={`w-5 h-5 ${isActive ? 'text-accent' : 'text-ink-faint'}`} />
                    </div>
                    <div>
                      <h4 className="bot-item-name">{draft?.name || 'VAT Watch'}</h4>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="bot-pill-agent">VAT Agent</span>
                        <span className={`bot-status-badge ${isActive ? 'active' : 'paused'}`}>
                          {isActive ? 'Active' : 'Paused'}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="bot-item-actions">
                    {onViewMemory && (
                      <button
                        type="button"
                        className="btn-icon-subtle"
                        onClick={() => onViewMemory(b)}
                        title="What this bot learned (Memory & Instructions)"
                      >
                        <Brain className="w-4 h-4 text-accent" />
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn-icon-subtle"
                      onClick={() => onToggle(b)}
                      title={isActive ? 'Pause bot' : 'Resume bot'}
                    >
                      {isActive ? (
                        <Pause className="w-4 h-4 text-warning" />
                      ) : (
                        <Play className="w-4 h-4 text-accent" />
                      )}
                    </button>
                    <button
                      type="button"
                      className="btn-icon-subtle"
                      onClick={() => onEdit(b)}
                      title="Edit bot settings"
                    >
                      <Edit2 className="w-4 h-4 text-ink-soft" />
                    </button>
                    <button
                      type="button"
                      className="btn-icon-subtle"
                      onClick={() => onDelete(id)}
                      title="Delete bot"
                    >
                      <Trash2 className="w-4 h-4 text-danger" />
                    </button>
                  </div>
                </div>

                <div className="bot-item-footer">
                  <div className="flex items-center gap-1.5 text-xs text-ink-soft">
                    <Clock className="w-3.5 h-3.5 text-ink-faint" />
                    <span>{schedText}</span>
                  </div>
                  <span className="bot-subtext">
                    {(draft?.watches || []).length} checks enabled
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
