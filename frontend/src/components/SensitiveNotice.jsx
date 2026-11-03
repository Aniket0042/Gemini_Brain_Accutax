import React from 'react';
import { ShieldAlert } from 'lucide-react';

// The warning above an answer when the question held a card number: it was removed before anything saw it.
// Shown from the moment the question is sent (the browser removed it) and kept with the saved turn
// (the server's "sensitive_notice" block).

export const SensitiveNotice = ({ kinds }) => {
  if (!kinds || !kinds.length) return null;
  return (
    <div role="alert" style={styles.wrap}>
      <ShieldAlert size={18} color="var(--warning-ink)" style={styles.icon} />
      <div>
        <div style={styles.title}>Card number removed</div>
        <div style={styles.body}>
          The card number in your message was removed before it was sent, so the AI never saw it and it is not
          saved. Do not share card numbers, passwords, OTPs or other sensitive details in this chat.
        </div>
      </div>
    </div>
  );
};

const styles = {
  wrap: {
    display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 12px', marginBottom: 10,
    background: 'var(--warning-soft)', border: '1px solid var(--warning)', borderRadius: 8,
  },
  icon: { flexShrink: 0, marginTop: 1 },
  title: { fontWeight: 600, fontSize: 13, color: 'var(--warning-ink)' },
  body: { fontSize: 13, lineHeight: 1.45, color: 'var(--warning-ink)', marginTop: 2 },
};
