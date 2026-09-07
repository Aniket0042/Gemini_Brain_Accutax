import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Sparkles, Building2, ChevronDown, Check, MessageSquare, Plus, ArrowLeft, Trash2 } from 'lucide-react';

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
  tenant,
  availableTenants = [],
  onSelectTenant,
  onNewSession,
  chatHistory = [],
  activeHistoryId = null,
  onSelectHistory,
  onDeleteHistory
}) => {
  const [hoveredHistoryId, setHoveredHistoryId] = useState(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = (t) => {
    if (onSelectTenant) onSelectTenant(t);
    setDropdownOpen(false);
  };

  const groupedHistory = useMemo(() => groupByTime(chatHistory), [chatHistory]);

  return (
    <div style={styles.sidebar}>
      <div style={styles.topSection}>
        {/* Branding */}
        <div style={styles.brandGroup}>
          <div style={styles.brandIcon}>
            <Sparkles size={14} color="#ffffff" />
          </div>
          <div>
            <h1 style={styles.brandTitle}>AccuTax AI</h1>
            <p style={styles.brandSubtitle}>Financial Intelligence</p>
          </div>
        </div>

        {/* New Session Button */}
        <button style={styles.newSessionBtn} onClick={onNewSession}>
          <Plus size={14} />
          New Session
        </button>

        {/* Tenant Selector Dropdown */}
        <div style={styles.dropdownContainer} ref={dropdownRef}>
          <button 
            style={styles.tenantBtn}
            onClick={() => setDropdownOpen(!dropdownOpen)}
          >
            <Sparkles size={12} color="#0e8a75" />
            <span style={styles.tenantName}>
              {tenant ? (tenant.display_name || tenant.org_name || `Org ${tenant.organization_id}`) : 'Select Tenant'}
            </span>
            <span style={styles.recommendedTag}>Recommended</span>
            <ChevronDown size={12} color="#94a3b8" style={{ marginLeft: 'auto' }} />
          </button>

          {dropdownOpen && (
            <div style={styles.dropdownMenu}>
              {availableTenants.map((t) => {
                const isSelected = tenant && (tenant.organization_id === t.id || tenant.organization_id === t.organization_id);
                return (
                  <div
                    key={t.id || t.organization_id}
                    style={{
                      ...styles.tenantItem,
                      backgroundColor: isSelected ? '#f1f5f9' : 'transparent',
                    }}
                    onClick={() => handleSelect(t)}
                  >
                    <span style={styles.tenantItemName}>{t.display_name || t.name}</span>
                    {isSelected && <Check size={14} color="#0e8a75" />}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Dynamic Chat History */}
      <div style={styles.historyList}>
        {groupedHistory.length === 0 ? (
          <div style={styles.emptyHistory}>
            <MessageSquare size={16} color="#cbd5e1" />
            <span>No conversations yet</span>
          </div>
        ) : (
          groupedHistory.map((group) => (
            <div key={group.section} style={styles.historyGroup}>
              <h3 style={styles.groupTitle}>{group.section}</h3>
              {group.items.map((item) => (
                <div
                  key={item.id}
                  style={{
                    ...styles.historyItem,
                    ...(item.id === activeHistoryId ? styles.historyItemActive : {}),
                  }}
                  onClick={() => onSelectHistory && onSelectHistory(item.id)}
                  onMouseEnter={() => setHoveredHistoryId(item.id)}
                  onMouseLeave={() => setHoveredHistoryId((prev) => (prev === item.id ? null : prev))}
                >
                  <MessageSquare size={13} color="#0A5C52" style={{ marginTop: '2px', flexShrink: 0 }} />
                  <div style={styles.historyText}>
                    <div style={styles.historyTitle}>{item.title}</div>
                    <div style={styles.historyPreview}>{item.preview}</div>
                  </div>
                  {hoveredHistoryId === item.id && (
                    <button
                      style={styles.historyDeleteBtn}
                      title="Delete session"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (onDeleteHistory) onDeleteHistory(item.id);
                      }}
                    >
                      <Trash2 size={13} color="#94a3b8" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {/* Bottom Action */}
      <div style={styles.bottomSection}>
        <button style={styles.backBtn} onClick={() => { window.location.href = 'https://accutax-bk-testing.netlify.app/'; }}>
          <ArrowLeft size={13} />
          Back to Dashboard
        </button>
      </div>
    </div>
  );
};

const styles = {
  sidebar: {
    width: '100%',
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    backgroundColor: '#ffffff',
  },
  topSection: {
    padding: '12px 12px 8px',
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  brandGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '2px',
  },
  brandIcon: {
    width: '30px',
    height: '30px',
    backgroundColor: '#0A5C52',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  brandTitle: {
    fontSize: '0.95rem',
    fontWeight: 700,
    color: '#1e293b',
    lineHeight: 1.2,
  },
  brandSubtitle: {
    fontSize: '0.65rem',
    color: '#94a3b8',
    fontWeight: 500,
  },
  newSessionBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '6px',
    width: '100%',
    padding: '8px',
    background: 'linear-gradient(to right, #0A5C52, #109383)',
    color: '#ffffff',
    border: 'none',
    borderRadius: '20px',
    fontWeight: 600,
    fontSize: '0.82rem',
    cursor: 'pointer',
    transition: 'opacity 0.2s',
  },
  tenantBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    width: '100%',
    padding: '7px 10px',
    backgroundColor: '#f8fafc',
    border: '1px solid #e2e8f0',
    borderRadius: '20px',
    cursor: 'pointer',
    fontSize: '0.78rem',
  },
  tenantName: {
    fontSize: '0.78rem',
    fontWeight: 500,
    color: '#1e293b',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '100px',
  },
  recommendedTag: {
    fontSize: '0.6rem',
    color: '#0e8a75',
    fontWeight: 600,
    marginLeft: 'auto',
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
    backgroundColor: '#ffffff',
    border: '1px solid #e2e8f0',
    borderRadius: '8px',
    boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
    marginTop: '4px',
    zIndex: 50,
    overflow: 'hidden',
  },
  tenantItem: {
    padding: '8px 10px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    cursor: 'pointer',
    borderBottom: '1px solid #f1f5f9',
    fontSize: '0.8rem',
  },
  tenantItemName: {
    fontSize: '0.8rem',
    color: '#1e293b',
  },
  historyList: {
    flex: 1,
    overflowY: 'auto',
    padding: '4px 12px',
  },
  emptyHistory: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '8px',
    padding: '32px 0',
    color: '#cbd5e1',
    fontSize: '0.78rem',
  },
  historyGroup: {
    marginBottom: '14px',
  },
  groupTitle: {
    fontSize: '0.62rem',
    fontWeight: 600,
    color: '#94a3b8',
    marginBottom: '6px',
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
  },
  historyItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '8px',
    marginBottom: '6px',
    cursor: 'pointer',
    padding: '5px 6px',
    borderRadius: '8px',
    transition: 'background-color 0.15s',
    position: 'relative',
  },
  historyItemActive: {
    backgroundColor: '#eef2f5',
  },
  historyDeleteBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: '2px',
    marginLeft: 'auto',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '4px',
  },
  historyText: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    flex: 1,
  },
  historyTitle: {
    fontSize: '0.78rem',
    color: '#334155',
    fontWeight: 500,
    marginBottom: '1px',
    lineHeight: 1.3,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  historyPreview: {
    fontSize: '0.68rem',
    color: '#94a3b8',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '170px',
  },
  bottomSection: {
    padding: '8px 12px',
    borderTop: '1px solid #f1f5f9',
  },
  backBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '6px',
    width: '100%',
    padding: '7px',
    backgroundColor: '#f8fafc',
    border: '1px solid #e2e8f0',
    borderRadius: '10px',
    color: '#475569',
    fontSize: '0.78rem',
    fontWeight: 500,
    cursor: 'pointer',
  }
};
