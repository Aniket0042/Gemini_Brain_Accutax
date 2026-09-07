import React from 'react';
import { PanelLeft, Plus } from 'lucide-react';

export const Header = ({ sessionTitle, onNewSession, onToggleSidebar }) => {
  return (
    <header style={styles.header}>
      <div style={styles.headerLeft}>
        <button style={styles.iconBtn} onClick={onToggleSidebar} title="Toggle Sidebar">
          <PanelLeft size={18} color="#64748b" />
        </button>
        {sessionTitle && (
          <div style={styles.titleContainer}>
            <div style={styles.greenDot} />
            <span style={styles.sessionTitle}>{sessionTitle}</span>
          </div>
        )}
      </div>

      <div style={styles.headerRight}>
        <button style={styles.newBtn} onClick={onNewSession} title="Start new session">
          <Plus size={14} color="#64748b" />
          <span>New</span>
        </button>
      </div>
    </header>
  );
};

const styles = {
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 24px',
    borderBottom: '1px solid #e2e8f0',
    backgroundColor: '#ffffff',
    width: '100%',
    height: '60px',
    boxSizing: 'border-box',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '16px',
  },
  iconBtn: {
    background: 'none',
    border: '1px solid #e2e8f0',
    borderRadius: '6px',
    padding: '6px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  titleContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  greenDot: {
    width: '8px',
    height: '8px',
    borderRadius: '50%',
    backgroundColor: '#10b981',
  },
  sessionTitle: {
    fontSize: '0.9rem',
    color: '#475569',
    fontWeight: 500,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '400px',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
  },
  newBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 12px',
    backgroundColor: '#ffffff',
    border: '1px solid #e2e8f0',
    borderRadius: '16px',
    color: '#475569',
    fontSize: '0.85rem',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'background-color 0.2s ease',
  },
};
