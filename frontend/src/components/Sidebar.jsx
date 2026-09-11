import React, { useState, useMemo, useRef, useEffect } from 'react';
import { Sparkles, Plus, ArrowLeft, Trash2, ChevronDown } from 'lucide-react';

/**
 * Groups chat history entries into time-based sections (Today, Yesterday, This Week, Earlier).
 */
function groupByTime(entries) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart);
  yesterdayStart.setDate(yesterdayStart.getDate() - 1);
  const weekStart = new Date(todayStart);
  weekStart.setDate(weekStart.getDate() - 7);

  const groups = { TODAY: [], YESTERDAY: [], 'THIS WEEK': [], EARLIER: [] };

  for (const entry of entries) {
    const ts = new Date(entry.timestamp);
    if (ts >= todayStart) {
      groups.TODAY.push(entry);
    } else if (ts >= yesterdayStart) {
      groups.YESTERDAY.push(entry);
    } else if (ts >= weekStart) {
      groups['THIS WEEK'].push(entry);
    } else {
      groups.EARLIER.push(entry);
    }
  }

  // Return only non-empty groups
  return Object.entries(groups)
    .filter(([, items]) => items.length > 0)
    .map(([section, items]) => ({ section, items }));
}

export const Sidebar = ({
  onNewSession,
  chatHistory = [],
  activeHistoryId = null,
  onSelectHistory,
  onDeleteHistory
}) => {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);
  const dropdownRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const groupedHistory = useMemo(() => groupByTime(chatHistory), [chatHistory]);

  return (
    <div style={styles.sidebar}>
      <div style={styles.topSection}>
        {/* Branding */}
        <div style={styles.brandRow}>
          <div style={styles.brandIcon}>
            <Sparkles size={20} color="var(--surface)" />
          </div>
          <div style={styles.brandTextGroup}>
            <span style={styles.brandTitle}>AccuTax AI</span>
            <span style={styles.brandSubtitle}>Financial Intelligence</span>
          </div>
        </div>

        {/* New Session Button */}
        <button className="control-btn" style={styles.newSessionBtn} onClick={onNewSession}>
          <Plus size={18} />
          New Session
        </button>

        {/* Model Selector Dropdown (matches screenshot design) */}
        <div style={styles.dropdownContainer} ref={dropdownRef}>
          <button
            className="control-btn"
            style={styles.tenantBtn}
            onClick={() => setDropdownOpen(!dropdownOpen)}
          >
            <Sparkles size={16} color="var(--accent)" />
            <span style={styles.tenantName}>AccuTax Pro</span>
            <span style={styles.recommendedBadge}>Recommended</span>
            <ChevronDown 
              size={14} 
              color="var(--ink-soft)" 
              style={{ flexShrink: 0, transform: dropdownOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} 
            />
          </button>

          {dropdownOpen && (
            <div style={styles.dropdownMenu}>
              <div style={styles.modelItem}>
                <Sparkles size={14} color="var(--accent)" style={styles.modelIcon} />
                <div style={styles.modelTextGroup}>
                  <div style={styles.modelTitle}>AccuTax Pro</div>
                  <div style={styles.modelDesc}>Best for financial analysis & tax</div>
                </div>
              </div>
              <div style={styles.modelItem}>
                <div style={styles.modelIconWrapper}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{color: "var(--ink-soft)"}}><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>
                </div>
                <div style={styles.modelTextGroup}>
                  <div style={styles.modelTitle}>AccuTax Agent</div>
                  <div style={styles.modelDesc}>Multi-step tasks & automation</div>
                </div>
              </div>
              <div style={styles.modelItem}>
                <div style={styles.modelIconWrapper}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{color: "var(--ink-soft)"}}><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                </div>
                <div style={styles.modelTextGroup}>
                  <div style={styles.modelTitle}>AccuTax Fast</div>
                  <div style={styles.modelDesc}>Instant answers & lookups</div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Dynamic Chat History — flat rows, one line each, no per-item icon
          or preview snippet. Matches how ChatGPT/Claude/Gemini render a
          history list: the title is the only thing shown until you open it. */}
      <div style={styles.historyList}>
        <h2 style={styles.recentHeading}>Recent chats</h2>
        {groupedHistory.length === 0 ? (
          <div style={styles.emptyHistory}>
            <span>No conversations yet</span>
          </div>
        ) : (
          groupedHistory.map((group) => (
            <div key={group.section} style={styles.historyGroup}>
              <h3 style={styles.groupTitle}>{group.section}</h3>
              {group.items.map((item) => (
                <div
                  key={item.id}
                  className="history-item"
                  style={styles.historyItem}
                  onClick={() => onSelectHistory && onSelectHistory(item.id)}
                >
                  <span
                    style={{
                      ...styles.historyTitle,
                      ...(item.id === activeHistoryId ? styles.historyTitleActive : {}),
                    }}
                  >
                    {item.title}
                  </span>
                  <button
                    className="history-delete-btn"
                    style={styles.historyDeleteBtn}
                    title="Delete session"
                    onClick={(e) => {
                      e.stopPropagation();
                      setPendingDelete(item);
                    }}
                  >
                    <Trash2 size={16} strokeWidth={2} />
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {/* Bottom Action */}
      <div style={styles.bottomSection}>
        <button
          className="control-btn"
          style={styles.backBtn}
          onClick={() => {
            const appUrl = (import.meta.env.VITE_ACCUTAX_APP_URL || 'http://localhost:5173').replace(/\/$/, '');
            window.location.href = `${appUrl}/`;
          }}
        >
          <ArrowLeft size={13} />
          Back to Dashboard
        </button>
      </div>

      {pendingDelete && (
        <div
          className="modal-overlay"
          onClick={() => setPendingDelete(null)}
          role="presentation"
        >
          <div
            className="modal-content"
            style={styles.deleteModal}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-chat-title"
          >
            <h3 id="delete-chat-title" style={styles.deleteTitle}>Delete this chat?</h3>
            <p style={styles.deleteBody}>
              “{pendingDelete.title || 'This conversation'}” will be removed from Recent chats. This cannot be undone.
            </p>
            <div style={styles.deleteActions}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setPendingDelete(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn"
                style={styles.deleteConfirm}
                onClick={() => {
                  const id = pendingDelete.id;
                  setPendingDelete(null);
                  if (onDeleteHistory) onDeleteHistory(id);
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const styles = {
  sidebar: {
    width: '100%',
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    backgroundColor: 'var(--sidebar-bg, var(--surface-2))',
  },
  topSection: {
    padding: '14px 10px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  brandRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '4px 4px',
    marginBottom: '12px',
  },
  brandIcon: {
    width: '30px',
    height: '30px',
    backgroundColor: 'var(--accent-ink)',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  brandTextGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
  },
  brandTitle: {
    fontSize: '0.875rem',
    fontWeight: 700,
    color: 'var(--ink)',
    lineHeight: 1,
  },
  brandSubtitle: {
    fontSize: '0.65rem',
    color: 'var(--ink-soft)',
    lineHeight: 1,
  },
  newSessionBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-start',
    gap: '10px',
    width: '100%',
    height: '36px',
    padding: '0 12px',
    background: 'linear-gradient(115deg, #0A5C52 60%, #109383)',
    color: 'var(--surface)',
    border: 'none',
    borderRadius: '18px',
    fontWeight: 600,
    fontSize: '0.8125rem',
    cursor: 'pointer',
    marginBottom: '8px',
    boxShadow: '0 2px 4px rgba(10, 92, 82, 0.2)',
  },
  tenantBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    width: '100%',
    height: '36px',
    padding: '0 12px',
    backgroundColor: 'var(--surface-2)',
    border: '1px solid var(--border-soft)',
    borderRadius: 'var(--radius-md)',
    cursor: 'pointer',
  },
  tenantName: {
    fontSize: '0.8125rem',
    fontWeight: 500,
    color: 'var(--ink)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  dropdownContainer: {
    position: 'relative',
    width: '100%',
  },
  dropdownMenu: {
    position: 'absolute',
    top: '100%',
    left: 0,
    width: '100%',
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-md)',
    marginTop: '6px',
    zIndex: 50,
    overflow: 'hidden',
    animation: 'menuIn var(--dur-base) var(--ease)',
    transformOrigin: 'top left',
  },
  recommendedBadge: {
    fontSize: '0.65rem',
    color: 'var(--accent)',
    fontWeight: 600,
    marginLeft: 'auto',
    marginRight: '6px',
    alignSelf: 'center',
  },
  modelItem: {
    padding: '8px 10px',
    display: 'flex',
    alignItems: 'flex-start',
    gap: '10px',
    cursor: 'pointer',
    transition: 'background-color var(--dur-fast) var(--ease)',
  },
  modelIconWrapper: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    marginTop: '2px',
    width: '16px',
  },
  modelIcon: {
    marginTop: '2px',
    flexShrink: 0,
  },
  modelTextGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    minWidth: 0,
  },
  modelTitle: {
    fontSize: '0.8125rem',
    fontWeight: 500,
    color: 'var(--ink)',
  },
  modelDesc: {
    fontSize: '0.6875rem',
    color: 'var(--ink-soft)',
    lineHeight: 1.3,
  },
  historyList: {
    flex: 1,
    overflowY: 'auto',
    padding: '6px 8px',
  },
  recentHeading: {
    fontSize: '0.95rem',
    fontWeight: 500,
    color: 'var(--ink)',
    margin: '6px 8px 10px',
    letterSpacing: '-0.01em',
  },
  emptyHistory: {
    display: 'flex',
    justifyContent: 'center',
    padding: '28px 0',
    color: 'var(--ink-faint)',
    fontSize: 'var(--text-sm)',
  },
  historyGroup: {
    marginBottom: '10px',
  },
  groupTitle: {
    fontSize: '0.75rem',
    fontWeight: 650,
    color: 'var(--ink-soft)',
    margin: '10px 8px 6px',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
  },
  // One line, no icon, no preview — no fill on the row in either theme.
  historyItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    cursor: 'pointer',
    padding: '7px 8px',
    minHeight: '32px',
    boxSizing: 'border-box',
    backgroundColor: 'transparent',
  },
  historyTitleActive: {
    color: 'var(--ink)',
    fontWeight: 400,
  },
  historyDeleteBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: 0,
    width: '24px',
    height: '24px',
    marginLeft: 'auto',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 'var(--radius-sm)',
  },
  historyTitle: {
    fontSize: '0.875rem',
    fontWeight: 400,
    color: 'var(--ink-soft)',
    lineHeight: 1.3,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    flex: 1,
    minWidth: 0,
  },
  deleteModal: {
    maxWidth: '420px',
    padding: '24px',
  },
  deleteTitle: {
    margin: '0 0 8px',
    fontSize: '1.05rem',
    fontWeight: 700,
    color: 'var(--ink)',
  },
  deleteBody: {
    margin: '0 0 20px',
    fontSize: '0.875rem',
    lineHeight: 1.45,
    color: 'var(--ink-soft)',
  },
  deleteActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: '10px',
  },
  deleteConfirm: {
    backgroundColor: '#dc2626',
    color: '#fff',
  },
  bottomSection: {
    padding: '8px',
    borderTop: '1px solid var(--border-soft)',
  },
  backBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '10px',
    width: '100%',
    height: '38px',
    padding: '0 16px',
    backgroundColor: 'var(--surface-2)',
    border: '1px solid var(--border-soft)',
    borderRadius: 'var(--radius-md)',
    color: 'var(--ink-soft)',
    fontSize: 'var(--text-sm)',
    fontWeight: 500,
    cursor: 'pointer',
  }
};
