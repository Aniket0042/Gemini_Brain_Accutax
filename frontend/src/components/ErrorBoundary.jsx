import React from 'react';

/**
 * A render error used to blank the whole app with no explanation. This shows what failed and lets the user
 * start again; the same text is in the browser console.
 */
export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('AccuTax AI crashed while drawing the page:', error, info && info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const reset = () => {
      try {
        sessionStorage.removeItem('accutax_active_session');
        localStorage.removeItem('accutax_chat_history');
      } catch { /* ignore */ }
      window.location.reload();
    };
    return (
      <div role="alert" style={{ maxWidth: 640, margin: '12vh auto', padding: 24, color: 'var(--ink, #e9eeec)', fontFamily: 'inherit' }}>
        <h2 style={{ margin: '0 0 8px' }}>Something went wrong drawing this page</h2>
        <p style={{ margin: '0 0 12px', opacity: 0.8 }}>Reload to try again. If it keeps happening, send this message to the team:</p>
        <pre style={{ whiteSpace: 'pre-wrap', padding: 12, borderRadius: 8, background: 'rgba(127,127,127,0.15)', fontSize: 13 }}>
          {String((error && (error.stack || error.message)) || error).slice(0, 1500)}
        </pre>
        <button type="button" onClick={() => window.location.reload()} style={{ marginRight: 8 }}>Reload</button>
        <button type="button" onClick={reset}>Clear saved chats on this device and reload</button>
      </div>
    );
  }
}
