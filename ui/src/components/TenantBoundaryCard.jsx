import React from 'react';
import { ShieldCheck, Lock, AlertTriangle, ChevronRight, Eye } from 'lucide-react';

export const TenantBoundaryCard = ({ tenant, onOpenAuthModal }) => {
  const isIsolated = Boolean(tenant && tenant.organization_id);

  return (
    <div style={styles.container} className="glass-panel">
      <div style={styles.leftGroup}>
        <div style={isIsolated ? styles.iconBoxIsolated : styles.iconBoxUnset}>
          {isIsolated ? <ShieldCheck size={22} color="#10b981" /> : <AlertTriangle size={22} color="#f43f5e" />}
        </div>
        <div>
          <div style={styles.titleRow}>
            <h4 style={styles.title}>
              {isIsolated ? `Tenant Boundary: ${tenant.org_name}` : 'Tenant Security Boundary Unset'}
            </h4>
            <span className={isIsolated ? 'badge badge-emerald' : 'badge badge-rose'}>
              {isIsolated ? 'ISOLATION ACTIVE' : 'UNAUTHENTICATED BLOCK'}
            </span>
          </div>

          <p style={styles.description}>
            {isIsolated ? (
              <>
                All requests automatically inject <strong>`organization_id: {tenant.organization_id}`</strong> into REST API calls and database SQL queries (<code>WHERE organization_id = {tenant.organization_id}</code>).
              </>
            ) : (
              <>
                No tenant context specified. Submitting a query will trigger an explicit <strong>`ValueError`</strong> from the backend security guard and block execution before any API or DB call.
              </>
            )}
          </p>
        </div>
      </div>

      <button className="btn btn-secondary" onClick={onOpenAuthModal} style={styles.switchBtn}>
        <span>{isIsolated ? 'Switch Tenant Context' : 'Select Tenant Context'}</span>
        <ChevronRight size={16} />
      </button>
    </div>
  );
};

const styles = {
  container: {
    padding: '16px 20px',
    marginBottom: '24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '16px',
    flexWrap: 'wrap',
  },
  leftGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
    flex: '1',
  },
  iconBoxIsolated: {
    width: '42px',
    height: '42px',
    borderRadius: '10px',
    backgroundColor: 'rgba(16, 185, 129, 0.12)',
    border: '1px solid rgba(16, 185, 129, 0.3)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  iconBoxUnset: {
    width: '42px',
    height: '42px',
    borderRadius: '10px',
    backgroundColor: 'rgba(244, 63, 94, 0.12)',
    border: '1px solid rgba(244, 63, 94, 0.3)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  titleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '4px',
    flexWrap: 'wrap',
  },
  title: {
    fontSize: '0.95rem',
    fontWeight: 600,
  },
  description: {
    fontSize: '0.8rem',
    color: '#9ca3af',
    lineHeight: '1.4',
  },
  switchBtn: {
    flexShrink: 0,
  },
};
