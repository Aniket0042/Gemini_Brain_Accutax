import React, { useState } from 'react';
import { Brain, Lock, Mail, ShieldCheck, Sparkles, AlertCircle, ArrowRight, Eye, EyeOff, Building2 } from 'lucide-react';
import { loginUser } from '../services/api';

const DEMO_ACCOUNTS = [
  {
    role: '🏢 Single-Tenant User',
    email: 'user_single@example.com',
    password: 'TestPass123!',
    assignedOrgs: [14],
    desc: 'Access strictly restricted to Org #14',
  },
  {
    role: '🏬 Multi-Tenant User',
    email: 'user_multi@example.com',
    password: 'TestPass123!',
    assignedOrgs: [14, 44],
    desc: 'Authorized to switch between Org #14 & Org #44',
  },
  {
    role: '👑 Admin User',
    email: 'admin@accutax.com',
    password: 'TestPass123!',
    assignedOrgs: [69, 27, 18, 14, 44],
    desc: 'Full access across all organization tenants',
  },
  {
    role: '🚫 Restricted / Zero-Org User',
    email: 'user_no_org@example.com',
    password: 'TestPass123!',
    assignedOrgs: [],
    desc: 'Demonstrates security isolation blocking queries',
  },
];

export const LoginPage = ({ onLoginSuccess }) => {
  const [email, setEmail] = useState('admin@accutax.com');
  const [password, setPassword] = useState('TestPass123!');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) return;

    setIsLoading(true);
    setError(null);

    try {
      const data = await loginUser(email, password);
      onLoginSuccess(data);
    } catch (err) {
      setError(err.message || 'Invalid email or password');
    } finally {
      setIsLoading(false);
    }
  };

  const handleAutofillDemo = (account) => {
    setEmail(account.email);
    setPassword(account.password);
    setError(null);
  };

  return (
    <div style={styles.loginContainer}>
      <div style={styles.loginCard} className="glass-panel">
        {/* Brand Header */}
        <div style={styles.brandHeader}>
          <div style={styles.brandIconBox}>
            <Brain size={32} color="#818cf8" />
          </div>
          <h2 style={styles.brandTitle}>Gemini Brain</h2>
          <p style={styles.brandSubtitle}>Production Multi-Tenant AI Financial Control Portal</p>
        </div>

        {/* Error Alert Banner */}
        {error && (
          <div style={styles.errorBox}>
            <AlertCircle size={18} color="#f43f5e" />
            <span>{error}</span>
          </div>
        )}

        {/* Login Form */}
        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.inputGroup}>
            <label style={styles.label}>Corporate Email Address</label>
            <div style={styles.inputWrapper}>
              <Mail size={18} color="#9ca3af" style={styles.inputIcon} />
              <input
                type="email"
                placeholder="name@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                style={styles.input}
                required
              />
            </div>
          </div>

          <div style={styles.inputGroup}>
            <label style={styles.label}>Password</label>
            <div style={styles.inputWrapper}>
              <Lock size={18} color="#9ca3af" style={styles.inputIcon} />
              <input
                type={showPassword ? 'text' : 'password'}
                placeholder="••••••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={styles.input}
                required
              />
              <button
                type="button"
                style={styles.eyeBtn}
                onClick={() => setShowPassword(!showPassword)}
              >
                {showPassword ? <EyeOff size={18} color="#9ca3af" /> : <Eye size={18} color="#9ca3af" />}
              </button>
            </div>
          </div>

          <button type="submit" className="btn btn-primary" style={styles.submitBtn} disabled={isLoading}>
            {isLoading ? (
              <>
                <Sparkles size={18} className="pulse-animation" />
                <span>Authenticating JWT & Tenant Claims...</span>
              </>
            ) : (
              <>
                <span>Sign In to Tenant Portal</span>
                <ArrowRight size={18} />
              </>
            )}
          </button>
        </form>

        {/* Quick Pre-Seeded Accounts Selector */}
        <div style={styles.demoSection}>
          <div style={styles.demoHeader}>
            <ShieldCheck size={14} color="#10b981" />
            <span>Pre-Seeded Multi-Tenant Demo Accounts</span>
          </div>

          <div style={styles.demoGrid}>
            {DEMO_ACCOUNTS.map((acc, idx) => (
              <div
                key={idx}
                style={styles.demoChip}
                onClick={() => handleAutofillDemo(acc)}
                title={acc.desc}
              >
                <div style={styles.demoChipTop}>
                  <span style={styles.demoRole}>{acc.role}</span>
                  <span className={acc.assignedOrgs.length > 0 ? "badge badge-emerald" : "badge badge-rose"}>
                    {acc.assignedOrgs.length > 0 ? `${acc.assignedOrgs.length} Org(s)` : 'No Orgs'}
                  </span>
                </div>
                <span style={styles.demoEmail}>{acc.email}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

const styles = {
  loginContainer: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px',
    background: 'radial-gradient(circle at 50% 30%, rgba(99, 102, 241, 0.12) 0%, transparent 60%)',
  },
  loginCard: {
    width: '100%',
    maxWidth: '460px',
    padding: '36px',
    borderRadius: '20px',
  },
  brandHeader: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    textAlign: 'center',
    marginBottom: '28px',
  },
  brandIconBox: {
    width: '56px',
    height: '56px',
    borderRadius: '16px',
    backgroundColor: 'rgba(99, 102, 241, 0.15)',
    border: '1px solid rgba(99, 102, 241, 0.3)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: '14px',
    boxShadow: '0 0 24px rgba(99, 102, 241, 0.25)',
  },
  brandTitle: {
    fontSize: '1.75rem',
    fontWeight: 700,
    marginBottom: '4px',
  },
  brandSubtitle: {
    fontSize: '0.85rem',
    color: '#9ca3af',
  },
  errorBox: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '12px 14px',
    borderRadius: '10px',
    backgroundColor: 'rgba(244, 63, 94, 0.12)',
    border: '1px solid rgba(244, 63, 94, 0.3)',
    color: '#f43f5e',
    fontSize: '0.85rem',
    marginBottom: '20px',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '18px',
  },
  inputGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  label: {
    fontSize: '0.825rem',
    fontWeight: 600,
    color: '#d1d5db',
  },
  inputWrapper: {
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
  },
  inputIcon: {
    position: 'absolute',
    left: '14px',
    pointerEvents: 'none',
  },
  input: {
    width: '100%',
    padding: '12px 14px 12px 42px',
    borderRadius: '10px',
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    color: '#ffffff',
    fontSize: '0.925rem',
    outline: 'none',
    transition: 'border-color 0.2s ease',
  },
  eyeBtn: {
    position: 'absolute',
    right: '12px',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
  },
  submitBtn: {
    width: '100%',
    padding: '12px',
    fontSize: '0.95rem',
    marginTop: '6px',
  },
  demoSection: {
    marginTop: '28px',
    paddingTop: '20px',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
  },
  demoHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#9ca3af',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    marginBottom: '12px',
  },
  demoGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '10px',
  },
  demoChip: {
    padding: '10px 12px',
    borderRadius: '10px',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  demoChipTop: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  demoRole: {
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#e5e7eb',
  },
  demoEmail: {
    fontSize: '0.725rem',
    color: '#9ca3af',
    fontFamily: 'var(--font-mono)',
  },
};
