import React, { useState } from 'react';
import { Brain, Lock, Mail, Sparkles, AlertCircle, ArrowRight, Eye, EyeOff, Building2, ShieldCheck } from 'lucide-react';
import { loginUser } from '../services/api';

export const LoginPage = ({ onLoginSuccess }) => {
  const [email, setEmail] = useState('genthird456@gmail.com');
  const [password, setPassword] = useState('Password123$$');
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

  const handleQuickFill = (demoEmail, demoPass) => {
    setEmail(demoEmail);
    setPassword(demoPass);
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
          <p style={styles.brandSubtitle}>AI Financial Orchestration & Multi-Tenant Portal</p>
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
            <label style={styles.label}>Email Address</label>
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
                <span>Authenticating with Accutax API...</span>
              </>
            ) : (
              <>
                <span>Sign In to Portal</span>
                <ArrowRight size={18} />
              </>
            )}
          </button>
        </form>

        {/* Quick Demo Credentials */}
        <div style={styles.quickFillSection}>
          <p style={styles.quickFillTitle}>
            <Building2 size={13} color="#818cf8" />
            <span>Preconfigured Multi-Tenant Accounts</span>
          </p>
          <div style={styles.quickFillGrid}>
            <button
              type="button"
              style={styles.quickFillBtn}
              onClick={() => handleQuickFill('genthird456@gmail.com', 'Password123$$')}
            >
              <div style={styles.quickFillBtnHeader}>
                <span style={styles.quickFillEmail}>genthird456@gmail.com</span>
                <span style={styles.quickFillBadge}>Accutax Live</span>
              </div>
              <span style={styles.quickFillDesc}>All 4 Orgs (27, 25, 154, 28)</span>
            </button>

            <button
              type="button"
              style={styles.quickFillBtn}
              onClick={() => handleQuickFill('admin_all@accutax.com', 'AdminPass123!')}
            >
              <div style={styles.quickFillBtnHeader}>
                <span style={styles.quickFillEmail}>admin_all@accutax.com</span>
                <span style={styles.quickFillBadge}>Admin</span>
              </div>
              <span style={styles.quickFillDesc}>Full Admin Tenant Access</span>
            </button>
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
    backgroundColor: 'rgba(15, 23, 42, 0.85)',
    backdropFilter: 'blur(20px)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    boxShadow: '0 20px 50px rgba(0, 0, 0, 0.5)',
  },
  brandHeader: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    textAlign: 'center',
    marginBottom: '24px',
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
    color: '#ffffff',
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
    gap: '16px',
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
    padding: '12px 42px 12px 42px',
    borderRadius: '10px',
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#ffffff',
    fontSize: '0.9rem',
    outline: 'none',
    transition: 'border-color 0.2s',
  },
  eyeBtn: {
    position: 'absolute',
    right: '12px',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: '4px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  submitBtn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    padding: '12px 20px',
    borderRadius: '10px',
    backgroundColor: '#6366f1',
    border: 'none',
    color: '#ffffff',
    fontSize: '0.95rem',
    fontWeight: 600,
    cursor: 'pointer',
    marginTop: '6px',
    boxShadow: '0 4px 14px rgba(99, 102, 241, 0.4)',
    transition: 'all 0.2s',
  },
  quickFillSection: {
    marginTop: '24px',
    paddingTop: '18px',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
  },
  quickFillTitle: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '0.75rem',
    fontWeight: 600,
    color: '#9ca3af',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    marginBottom: '10px',
  },
  quickFillGrid: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  quickFillBtn: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    padding: '8px 12px',
    borderRadius: '8px',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    textAlign: 'left',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  },
  quickFillBtnHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  quickFillEmail: {
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#e2e8f0',
  },
  quickFillBadge: {
    fontSize: '0.65rem',
    fontWeight: 600,
    color: '#818cf8',
    backgroundColor: 'rgba(99, 102, 241, 0.15)',
    padding: '1px 6px',
    borderRadius: '4px',
  },
  quickFillDesc: {
    fontSize: '0.7rem',
    color: '#94a3b8',
  },
};
