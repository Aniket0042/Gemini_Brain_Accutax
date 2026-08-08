import React, { useState } from 'react';
import { ShieldCheck, Building2, User, Database, AlertTriangle, CheckCircle2, X } from 'lucide-react';

const TENANT_PRESETS = [
  {
    organization_id: 69,
    org_name: 'Accutax LLC',
    user_id: 9,
    db_name: 'accutax_bk_1_5',
    description: 'Primary Tenant — UAE Accounting & Invoicing',
  },
  {
    organization_id: 27,
    org_name: 'Manufacturing Corp',
    user_id: 18,
    db_name: 'accutax_bk',
    description: 'Secondary Tenant — Supply Chain & Expenses',
  },
  {
    organization_id: 18,
    org_name: 'TechSolutions Inc',
    user_id: 18,
    db_name: 'accutax_bk',
    description: 'Enterprise Tenant — IT & Services',
  },
  {
    organization_id: null,
    org_name: '⚠️ Unauthenticated / Missing Org Context',
    user_id: 18,
    db_name: 'accutax_bk',
    description: 'Security Demo — Proves tenant isolation blocks requests with no org ID',
  },
];

export const TenantLoginModal = ({ currentTenant, onSelectTenant, onClose }) => {
  const [selectedOrgId, setSelectedOrgId] = useState(currentTenant?.organization_id ?? 69);
  const [customOrgId, setCustomOrgId] = useState('');
  const [customUserId, setCustomUserId] = useState(currentTenant?.user_id || 18);
  const [customDbName, setCustomDbName] = useState(currentTenant?.db_name || 'accutax_bk');
  const [isCustom, setIsCustom] = useState(false);

  const handleApplyPreset = (preset) => {
    onSelectTenant({
      organization_id: preset.organization_id,
      org_name: preset.org_name,
      user_id: preset.user_id,
      db_name: preset.db_name,
    });
    onClose();
  };

  const handleApplyCustom = (e) => {
    e.preventDefault();
    const orgIdNum = customOrgId ? parseInt(customOrgId, 10) : null;
    onSelectTenant({
      organization_id: orgIdNum,
      org_name: orgIdNum ? `Tenant Organization ${orgIdNum}` : 'Unset Tenant Context',
      user_id: parseInt(customUserId, 10) || 18,
      db_name: customDbName || 'accutax_bk',
    });
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div style={styles.modalHeader}>
          <div style={styles.modalTitleBox}>
            <ShieldCheck size={24} color="#818cf8" />
            <div>
              <h3>Tenant Authentication & Security Context</h3>
              <p style={styles.modalSub}>Select or switch tenant organization context</p>
            </div>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>
            <X size={20} color="#9ca3af" />
          </button>
        </div>

        <div style={styles.presetsList}>
          {TENANT_PRESETS.map((preset, idx) => {
            const isSelected = !isCustom && currentTenant?.organization_id === preset.organization_id;
            const isWarning = preset.organization_id === null;

            return (
              <div
                key={idx}
                style={{
                  ...styles.presetCard,
                  ...(isSelected ? styles.presetCardSelected : {}),
                  ...(isWarning ? styles.presetCardWarning : {}),
                }}
                onClick={() => {
                  setIsCustom(false);
                  handleApplyPreset(preset);
                }}
              >
                <div style={styles.presetTop}>
                  <div style={styles.presetNameGroup}>
                    {isWarning ? <AlertTriangle size={18} color="#f43f5e" /> : <Building2 size={18} color="#10b981" />}
                    <span style={styles.presetName}>{preset.org_name}</span>
                  </div>
                  {isSelected && <CheckCircle2 size={18} color="#10b981" />}
                </div>

                <p style={styles.presetDesc}>{preset.description}</p>

                <div style={styles.presetMetaRow}>
                  <span className={isWarning ? "badge badge-rose" : "badge badge-emerald"}>
                    Org ID: {preset.organization_id !== null ? preset.organization_id : "NULL"}
                  </span>
                  <span className="badge badge-primary">User ID: {preset.user_id}</span>
                  <span className="badge badge-cyan">DB: {preset.db_name}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Custom Input Accordion */}
        <div style={styles.customSection}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ width: '100%' }}
            onClick={() => setIsCustom(!isCustom)}
          >
            {isCustom ? 'Hide Custom Credentials' : '⚙️ Custom Organization Credentials'}
          </button>

          {isCustom && (
            <form onSubmit={handleApplyCustom} style={styles.customForm}>
              <div style={styles.inputGroup}>
                <label style={styles.label}>Organization ID:</label>
                <input
                  type="number"
                  placeholder="e.g. 69 (Leave empty to test missing tenant isolation)"
                  value={customOrgId}
                  onChange={(e) => setCustomOrgId(e.target.value)}
                  style={styles.input}
                />
              </div>

              <div style={styles.inputGroup}>
                <label style={styles.label}>User ID:</label>
                <input
                  type="number"
                  value={customUserId}
                  onChange={(e) => setCustomUserId(e.target.value)}
                  style={styles.input}
                />
              </div>

              <div style={styles.inputGroup}>
                <label style={styles.label}>Database Name:</label>
                <input
                  type="text"
                  value={customDbName}
                  onChange={(e) => setCustomDbName(e.target.value)}
                  style={styles.input}
                />
              </div>

              <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '10px' }}>
                Apply Custom Tenant Context
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};

const styles = {
  modalHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: '20px',
  },
  modalTitleBox: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  modalSub: {
    fontSize: '0.8rem',
    color: '#9ca3af',
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
  },
  presetsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
    marginBottom: '20px',
  },
  presetCard: {
    padding: '14px 16px',
    borderRadius: '12px',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  presetCardSelected: {
    borderColor: '#10b981',
    backgroundColor: 'rgba(16, 185, 129, 0.08)',
  },
  presetCardWarning: {
    borderColor: 'rgba(244, 63, 94, 0.3)',
    backgroundColor: 'rgba(244, 63, 94, 0.05)',
  },
  presetTop: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: '6px',
  },
  presetNameGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  presetName: {
    fontWeight: 600,
    fontSize: '0.95rem',
  },
  presetDesc: {
    fontSize: '0.8rem',
    color: '#9ca3af',
    marginBottom: '10px',
  },
  presetMetaRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    flexWrap: 'wrap',
  },
  customSection: {
    marginTop: '16px',
  },
  customForm: {
    marginTop: '14px',
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  inputGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  label: {
    fontSize: '0.78rem',
    color: '#9ca3af',
    fontWeight: 500,
  },
  input: {
    padding: '10px 12px',
    borderRadius: '8px',
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(255, 255, 255, 0.15)',
    color: '#ffffff',
    fontSize: '0.9rem',
    outline: 'none',
  },
};
