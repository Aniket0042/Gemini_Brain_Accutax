import React from 'react';
import { PanelLeft, Plus, Sun, Moon, MonitorSmartphone, Activity } from 'lucide-react';
import { TenantSwitcher } from './TenantSwitcher';

// Icon and label per theme state — cycling System → Light → Dark → System,
// same order and single-icon-button pattern as the reference products.
const THEME_META = {
  system: { icon: MonitorSmartphone, label: 'Matching system' },
  light: { icon: Sun, label: 'Light' },
  dark: { icon: Moon, label: 'Dark' },
};

export const Header = ({
  sessionTitle,
  onNewSession,
  onToggleSidebar,
  theme = 'system',
  onCycleTheme,
  activeTenant,
  availableTenants,
  onSelectTenant,
  onOpenHealthModal,
}) => {
  const { icon: ThemeIcon, label: themeLabel } = THEME_META[theme] || THEME_META.system;

  return (
    <header style={styles.header}>
      <div style={styles.headerLeft}>
        <button className="icon-btn" style={styles.iconBtn} onClick={onToggleSidebar} title="Toggle Sidebar">
          <PanelLeft size={18} color="var(--ink-soft)" />
        </button>
        <TenantSwitcher tenant={activeTenant} availableTenants={availableTenants} onSelectTenant={onSelectTenant} />
        {sessionTitle && (
          <div style={styles.titleContainer}>
            <div style={styles.greenDot} />
            <span style={styles.sessionTitle}>{sessionTitle}</span>
          </div>
        )}
      </div>

      <div style={styles.headerRight}>
        {onOpenHealthModal && (
          <button
            className="icon-btn"
            style={styles.iconBtn}
            onClick={onOpenHealthModal}
            title="Check AI service health diagnostics"
          >
            <Activity size={16} color="var(--ink-soft)" />
          </button>
        )}
        {onCycleTheme && (
          <button
            className="icon-btn"
            style={styles.iconBtn}
            onClick={onCycleTheme}
            title={`Theme: ${themeLabel} — click to change`}
          >
            <ThemeIcon size={16} color="var(--ink-soft)" />
          </button>
        )}
        <button className="control-btn" style={styles.newBtn} onClick={onNewSession} title="Start new session">
          <Plus size={14} color="var(--ink-soft)" />
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
    padding: '0 20px',
    borderBottom: '1px solid var(--border-soft)',
    backgroundColor: 'var(--header-bg, var(--bg))',
    width: '100%',
    height: '52px',
    boxSizing: 'border-box',
    flexShrink: 0,
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
    minWidth: 0,
  },
  // Icon-only control — the --control-h-sm tier, used for every icon button
  // in the header and reused verbatim by the sidebar's own icon buttons.
  iconBtn: {
    width: 'var(--control-h-sm)',
    height: 'var(--control-h-sm)',
    background: 'none',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    padding: 0,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'background-color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)',
  },
  titleContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    minWidth: 0,
  },
  greenDot: {
    width: '7px',
    height: '7px',
    borderRadius: '50%',
    backgroundColor: 'var(--success)',
    flexShrink: 0,
  },
  sessionTitle: {
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-soft)',
    fontWeight: 500,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '400px',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  // Standard row control — the --control-h tier, matching the sidebar's
  // "New Session" / "Back to Dashboard" buttons exactly.
  newBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    height: 'var(--control-h)',
    padding: '0 var(--control-pad-x)',
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-pill)',
    color: 'var(--ink-soft)',
    fontSize: 'var(--text-sm)',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'background-color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)',
  },
};
