import React, { useState, useEffect } from 'react';
import { Activity, Cpu, Database, Server, RefreshCw, CheckCircle2, XCircle, Clock, X } from 'lucide-react';
import { fetchModelHealth } from '../services/api';

export const ModelHealthModal = ({ onClose }) => {
  const [healthData, setHealthData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadDiagnostics = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchModelHealth();
      setHealthData(data);
    } catch (err) {
      setError(err.message || 'Failed to fetch diagnostics');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDiagnostics();
  }, []);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={{ maxWidth: '680px' }} onClick={(e) => e.stopPropagation()}>
        {/* Modal Header */}
        <div style={styles.header}>
          <div style={styles.titleGroup}>
            <Activity size={24} color="#06b6d4" />
            <div>
              <h3>AI Models & Services Health Diagnostics</h3>
              <p style={styles.subtitle}>Real-time connection & latency metrics across all engines</p>
            </div>
          </div>
          <div style={styles.headerRight}>
            <button className="btn btn-secondary" onClick={loadDiagnostics} disabled={isLoading}>
              <RefreshCw size={14} className={isLoading ? 'pulse-animation' : ''} />
              <span>Refresh</span>
            </button>
            <button style={styles.closeBtn} onClick={onClose}>
              <X size={20} color="#9ca3af" />
            </button>
          </div>
        </div>

        {/* Loading / Error States */}
        {isLoading && !healthData && (
          <div style={styles.loadingBox}>
            <RefreshCw size={24} color="#6366f1" className="pulse-animation" />
            <p>Pinging Google Gemini, AWS Bedrock, Accutax REST API, and PostgreSQL DB...</p>
          </div>
        )}

        {error && (
          <div style={styles.errorBox}>
            <XCircle size={20} color="#f43f5e" />
            <span>{error}</span>
          </div>
        )}

        {/* Diagnostic Results */}
        {healthData && (
          <div style={styles.diagnosticsContainer}>
            {/* Overall Status Banner */}
            <div
              style={{
                ...styles.statusBanner,
                backgroundColor: healthData.overall_status === 'ok' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)',
                borderColor: healthData.overall_status === 'ok' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(245, 158, 11, 0.3)',
              }}
            >
              <div style={styles.statusBannerLeft}>
                {healthData.overall_status === 'ok' ? (
                  <CheckCircle2 size={22} color="#10b981" />
                ) : (
                  <Activity size={22} color="#f59e0b" />
                )}
                <div>
                  <h4 style={{ color: healthData.overall_status === 'ok' ? '#10b981' : '#f59e0b' }}>
                    System Status: {healthData.overall_status.toUpperCase()}
                  </h4>
                  <p style={styles.statusSub}>
                    AI Models: {healthData.summary.models_healthy}/{healthData.summary.models_tested} Healthy • Services: {healthData.summary.services_healthy}/{healthData.summary.services_tested} Healthy
                  </p>
                </div>
              </div>
              <span className={healthData.overall_status === 'ok' ? 'badge badge-emerald' : 'badge badge-amber'}>
                {healthData.overall_status === 'ok' ? '100% OPERATIONAL' : 'DEGRADED'}
              </span>
            </div>

            {/* AI Models Section */}
            <div style={styles.sectionHeader}>
              <Cpu size={16} color="#818cf8" />
              <span>Configured AI Models</span>
            </div>

            <div style={styles.grid}>
              {healthData.models.map((model, idx) => {
                const isOk = model.status === 'ok';
                return (
                  <div key={idx} style={styles.card}>
                    <div style={styles.cardTop}>
                      <div style={styles.cardTitleGroup}>
                        {isOk ? <CheckCircle2 size={16} color="#10b981" /> : <XCircle size={16} color="#f43f5e" />}
                        <span style={styles.cardName}>{model.name}</span>
                      </div>
                      <span className={isOk ? 'badge badge-emerald' : 'badge badge-rose'}>
                        {isOk ? 'OK' : 'ERROR'}
                      </span>
                    </div>

                    <p style={styles.cardModelId}>{model.model_id}</p>

                    <div style={styles.cardMetaRow}>
                      <span style={styles.metaItem}>
                        <Clock size={12} color="#9ca3af" />
                        <span>{model.latency_ms} ms</span>
                      </span>
                      {model.sample_response && (
                        <span style={styles.sampleText}>Output: "{model.sample_response}"</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Backend Services Section */}
            <div style={{ ...styles.sectionHeader, marginTop: '20px' }}>
              <Server size={16} color="#06b6d4" />
              <span>Backend Services & Database</span>
            </div>

            <div style={styles.grid}>
              {healthData.services.map((service, idx) => {
                const isOk = service.status === 'ok';
                return (
                  <div key={idx} style={styles.card}>
                    <div style={styles.cardTop}>
                      <div style={styles.cardTitleGroup}>
                        {isOk ? <CheckCircle2 size={16} color="#10b981" /> : <XCircle size={16} color="#f43f5e" />}
                        <span style={styles.cardName}>{service.service}</span>
                      </div>
                      <span className={isOk ? 'badge badge-emerald' : 'badge badge-rose'}>
                        {isOk ? 'OK' : 'ERROR'}
                      </span>
                    </div>

                    <p style={styles.cardModelId}>{service.target}</p>

                    <div style={styles.cardMetaRow}>
                      <span style={styles.metaItem}>
                        <Clock size={12} color="#9ca3af" />
                        <span>{service.latency_ms} ms</span>
                      </span>
                      {service.http_code && (
                        <span style={styles.sampleText}>HTTP {service.http_code}</span>
                      )}
                    </div>
                    {service.error && <p style={styles.errorText}>{service.error}</p>}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const styles = {
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: '20px',
  },
  titleGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  subtitle: {
    fontSize: '0.8rem',
    color: '#9ca3af',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
  },
  loadingBox: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '12px',
    padding: '40px',
    color: '#9ca3af',
  },
  errorBox: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '14px',
    borderRadius: '10px',
    backgroundColor: 'rgba(244, 63, 94, 0.1)',
    color: '#f43f5e',
    fontSize: '0.875rem',
    marginBottom: '16px',
  },
  diagnosticsContainer: {
    display: 'flex',
    flexDirection: 'column',
  },
  statusBanner: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '16px 20px',
    borderRadius: '12px',
    border: '1px solid',
    marginBottom: '20px',
  },
  statusBannerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  statusSub: {
    fontSize: '0.8rem',
    color: '#9ca3af',
    marginTop: '2px',
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    fontSize: '0.85rem',
    fontWeight: 600,
    color: '#d1d5db',
    marginBottom: '12px',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
    gap: '12px',
  },
  card: {
    padding: '14px',
    borderRadius: '10px',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
  },
  cardTop: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: '6px',
  },
  cardTitleGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  cardName: {
    fontSize: '0.9rem',
    fontWeight: 600,
    color: '#f3f4f6',
  },
  cardModelId: {
    fontSize: '0.75rem',
    color: '#9ca3af',
    fontFamily: 'var(--font-mono)',
    marginBottom: '10px',
  },
  cardMetaRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  metaItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '0.78rem',
    color: '#9ca3af',
  },
  sampleText: {
    fontSize: '0.78rem',
    color: '#10b981',
    fontWeight: 500,
  },
  errorText: {
    fontSize: '0.75rem',
    color: '#f43f5e',
    marginTop: '6px',
    fontFamily: 'var(--font-mono)',
  },
};
