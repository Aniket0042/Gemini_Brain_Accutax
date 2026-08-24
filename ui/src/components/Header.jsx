import React, { useState, useRef, useEffect } from 'react';
import { ShieldCheck, Activity, Brain, Building2, LogOut, UserCheck, ChevronDown, Check, Sparkles } from 'lucide-react';

export const Header = ({ 
  tenant, 
  availableTenants = [], 
  currentUser, 
  onSelectTenant,
  onOpenHealthModal, 
  onLogout 
}) => {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);
  const isTenantValid = Boolean(tenant && tenant.organization_id);

  // Close dropdown on outside click
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
    if (onSelectTenant) {
      onSelectTenant(t);
    }
    setDropdownOpen(false);
  };

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
            <div style={styles.userBadge} title={`Logged in as: ${currentUser.email}`}>
              <UserCheck size={14} color="#818cf8" />
              <span style={styles.userEmail}>{currentUser.email}</span>
            </div>
          )}

          {/* Interactive Organization Selector Dropdown */}
          <div style={styles.dropdownContainer} ref={dropdownRef}>
            <button 
              style={{
                ...styles.tenantPillButton,
                borderColor: dropdownOpen ? '#818cf8' : 'rgba(16, 185, 129, 0.3)',
                backgroundColor: dropdownOpen ? 'rgba(99, 102, 241, 0.12)' : 'rgba(16, 185, 129, 0.06)',
              }}
              onClick={() => setDropdownOpen(!dropdownOpen)}
              title="Click to switch organization"
            >
              <Building2 size={14} color={isTenantValid ? "#10b981" : "#f43f5e"} />
              <div style={styles.tenantInfo}>
                <span style={styles.tenantName}>
                  {tenant ? (tenant.display_name || tenant.org_name || `Org ${tenant.organization_id}`) : 'Select Tenant'}
                </span>
                {tenant && tenant.tag && (
                  <span style={styles.tenantTag}>{tenant.tag}</span>
                )}
              </div>
              <span style={styles.isolatedTag}>
                {isTenantValid ? `ID: ${tenant.organization_id}` : "Unset"}
              </span>
              <ChevronDown 
                size={14} 
                color="#9ca3af" 
                style={{ 
                  transform: dropdownOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                  transition: 'transform 0.2s ease',
                  marginLeft: '2px'
                }} 
              />
            </button>

            {/* Dropdown Menu */}
            {dropdownOpen && (
              <div style={styles.dropdownMenu}>
                <div style={styles.dropdownHeader}>
                  <Sparkles size={13} color="#818cf8" />
                  <span>Switch Active Tenant</span>
                </div>
                <div style={styles.tenantList}>
                  {availableTenants.map((t) => {
                    const isSelected = tenant && (tenant.organization_id === t.id || tenant.organization_id === t.organization_id);
                    return (
                      <div
                        key={t.id || t.organization_id}
                        style={{
                          ...styles.tenantItem,
                          backgroundColor: isSelected ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
                          borderColor: isSelected ? 'rgba(99, 102, 241, 0.35)' : 'transparent',
                        }}
                        onClick={() => handleSelect(t)}
                      >
                        <div style={styles.itemLeft}>
                          <div style={styles.itemHeader}>
                            <span style={styles.itemOrgIdBadge}>Org {t.id || t.organization_id}</span>
                            <span style={styles.itemDisplayName}>{t.display_name || t.name}</span>
                            {t.tag && <span style={styles.itemSpecialtyTag}>{t.tag}</span>}
                          </div>
                          {t.description && (
                            <p style={styles.itemDescription}>{t.description}</p>
                          )}
                        </div>
                        {isSelected && (
                          <div style={styles.checkIcon}>
                            <Check size={14} color="#818cf8" />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Diagnostics Button */}
          <button style={styles.actionBtn} onClick={onOpenHealthModal} title="Check AI Service Health Diagnostics">
            <Activity size={14} color="#06b6d4" />
            <span>Health</span>
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
    backgroundColor: 'rgba(11, 15, 25, 0.88)',
    backdropFilter: 'blur(16px)',
    position: 'sticky',
    top: 0,
    zIndex: 50,
    padding: '8px 20px',
  },
  headerInner: {
    maxWidth: '1180px',
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
  dropdownContainer: {
    position: 'relative',
  },
  tenantPillButton: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '5px 12px',
    borderRadius: '9px',
    border: '1px solid',
    cursor: 'pointer',
    userSelect: 'none',
    transition: 'all 0.2s ease',
    color: '#ffffff',
  },
  tenantInfo: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    textAlign: 'left',
  },
  tenantName: {
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#e5e7eb',
  },
  tenantTag: {
    fontSize: '0.65rem',
    color: '#10b981',
    fontWeight: 500,
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
  dropdownMenu: {
    position: 'absolute',
    top: 'calc(100% + 8px)',
    right: 0,
    width: '380px',
    backgroundColor: 'rgba(15, 23, 42, 0.96)',
    backdropFilter: 'blur(20px)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    borderRadius: '12px',
    boxShadow: '0 12px 36px rgba(0, 0, 0, 0.5), 0 0 1px rgba(255, 255, 255, 0.2)',
    padding: '8px',
    zIndex: 100,
    animation: 'fadeIn 0.15s ease-out',
  },
  dropdownHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 10px 8px 10px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
    fontSize: '0.72rem',
    fontWeight: 600,
    color: '#94a3b8',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
  },
  tenantList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    marginTop: '6px',
  },
  tenantItem: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '8px 10px',
    borderRadius: '8px',
    border: '1px solid',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  },
  itemLeft: {
    display: 'flex',
    flexDirection: 'column',
    gap: '3px',
    flex: 1,
  },
  itemHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    flexWrap: 'wrap',
  },
  itemOrgIdBadge: {
    fontSize: '0.68rem',
    fontWeight: 700,
    color: '#818cf8',
    backgroundColor: 'rgba(99, 102, 241, 0.15)',
    padding: '1px 5px',
    borderRadius: '4px',
  },
  itemDisplayName: {
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#f1f5f9',
  },
  itemSpecialtyTag: {
    fontSize: '0.65rem',
    fontWeight: 500,
    color: '#10b981',
    backgroundColor: 'rgba(16, 185, 129, 0.12)',
    padding: '1px 5px',
    borderRadius: '4px',
  },
  itemDescription: {
    fontSize: '0.68rem',
    color: '#94a3b8',
    lineHeight: '1.3',
    margin: 0,
  },
  checkIcon: {
    marginLeft: '8px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
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
