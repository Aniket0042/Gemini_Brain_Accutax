import React from 'react';
import { ShieldCheck, Activity, Brain, Building2, LogOut, UserCheck } from 'lucide-react';

export const Header = ({ tenant, currentUser, onOpenAuthModal, onOpenHealthModal, onLogout }) => {
  const isTenantValid = Boolean(tenant && tenant.organization_id);

  return (
    <header style={styles.header}>
      <div style={styles.headerInner}>
        {/* Brand Group */}
        <div style={styles.brandGroup}>
          <div style={styles.iconBox}>
            <Brain size={20} color="#818cf8" />
          </div>
          <div>
            <h1 style={styles.title}>Gemini Brain</h1>
            <p style={styles.subtitle}>AI Financial Orchestration</p>
          </div>
        </div>

        {/* Actions / Information Group */}
        <div style={styles.actionsGroup}>
          {/* User Email & Identity Badge */}
          {currentUser && (
            <div style={styles.userBadge} title={`User: ${currentUser.email}`}>
              <UserCheck size={14} color="#818cf8" />
              <span style={styles.userEmail}>{currentUser.email}</span>
            </div>
          )}

          {/* Active Tenant Context pill */}
          <div style={styles.tenantPill} onClick={onOpenAuthModal} title="Click to switch organization context">
            <Building2 size={14} color={isTenantValid ? "#10b981" : "#f43f5e"} />
            {isTenantValid ? (
              <span style={styles.tenantName}>
                {tenant.org_name.split(' (')[0]}
              </span>
            ) : (
              <span style={styles.noTenantText}>No Context</span>
            )}
            <span style={styles.isolatedTag}>
              {isTenantValid ? `Isolated (ID: ${tenant.organization_id})` : "Unset"}
            </span>
          </div>

          {/* Diagnostics Button */}
          <button style={styles.actionBtn} onClick={onOpenHealthModal} title="Check AI Service Health Diagnostics">
            <Activity size={14} color="#06b6d4" />
            <span>Health</span>
          </button>

          {/* Switch Tenant Icon Button */}
          <button style={styles.primaryBtn} onClick={onOpenAuthModal} title="Switch Tenant Organization">
            <ShieldCheck size={14} />
            <span>Switch</span>
          </button>

          {/* Log Out Button */}
          {onLogout && (
            <button style={styles.dangerBtn} onClick={onLogout} title="Sign Out">
              <LogOut size={14} />
            </button>
          )}
        </div>
      </div>
    </header>
  );
};

const styles = {
  header: {
    width: '100%',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
    backgroundColor: 'rgba(11, 15, 25, 0.85)',
    backdropFilter: 'blur(16px)',
    position: 'sticky',
    top: 0,
    zIndex: 50,
    padding: '8px 20px',
  },
  headerInner: {
    maxWidth: '1150px',
    width: '100%',
    margin: '0 auto',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '16px',
  },
  brandGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  iconBox: {
    width: '34px',
    height: '34px',
    borderRadius: '10px',
    backgroundColor: 'rgba(99, 102, 241, 0.15)',
    border: '1px solid rgba(99, 102, 241, 0.3)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    boxShadow: '0 0 16px rgba(99, 102, 241, 0.15)',
  },
  title: {
    fontSize: '1.1rem',
    fontWeight: 700,
    lineHeight: '1.1',
    color: '#ffffff',
  },
  subtitle: {
    fontSize: '0.72rem',
    color: '#9ca3af',
    fontWeight: 500,
  },
  actionsGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  userBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '5px 10px',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: '9px',
  },
  userEmail: {
    fontSize: '0.75rem',
    fontWeight: 500,
    color: '#d1d5db',
  },
  tenantPill: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '5px 10px',
    backgroundColor: 'rgba(16, 185, 129, 0.06)',
    border: '1px solid rgba(16, 185, 129, 0.2)',
    borderRadius: '9px',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  tenantName: {
    fontSize: '0.75rem',
    fontWeight: 600,
    color: '#e5e7eb',
  },
  isolatedTag: {
    fontSize: '0.68rem',
    fontWeight: 600,
    textTransform: 'uppercase',
    color: '#10b981',
    backgroundColor: 'rgba(16, 185, 129, 0.15)',
    padding: '2px 6px',
    borderRadius: '6px',
  },
  noTenantText: {
    fontSize: '0.75rem',
    color: '#f43f5e',
    fontWeight: 600,
  },
  actionBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '5px 10px',
    borderRadius: '9px',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#e5e7eb',
    fontSize: '0.75rem',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  primaryBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '5px 10px',
    borderRadius: '9px',
    backgroundColor: '#818cf8',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#ffffff',
    fontSize: '0.75rem',
    fontWeight: 600,
    cursor: 'pointer',
    boxShadow: '0 2px 8px rgba(129, 140, 248, 0.3)',
    transition: 'all 0.2s ease',
  },
  dangerBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '28px',
    height: '28px',
    borderRadius: '9px',
    backgroundColor: 'rgba(244, 63, 94, 0.1)',
    border: '1px solid rgba(244, 63, 94, 0.2)',
    color: '#f43f5e',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
};
