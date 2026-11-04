import { useEffect, useState } from 'react';
import App from '../App';
import { LegacyLoginPage } from '../components/LegacyLoginPage';
import { clearFinanceStorage, sessionClient } from './session';
import { clearUploadBlobs } from '../services/uploadBlobs';

// The VM's Accutax dashboard opens the full chat as a popup (?handoff=1) and posts its token and the
// conversation to it. Only that dashboard's origin may hand over.
const HANDOFF_WAIT_MS = 15000;

function dashboardOrigins() {
  try {
    const url = new URL(import.meta.env.VITE_ACCUTAX_APP_URL || 'http://localhost:5173');
    const hosts = new Set([url.hostname]);
    if (url.hostname === 'localhost') hosts.add('127.0.0.1');
    if (url.hostname === '127.0.0.1') hosts.add('localhost');
    const port = url.port ? `:${url.port}` : '';
    return [...hosts].map((host) => `${url.protocol}//${host}${port}`);
  } catch {
    return [];
  }
}

function wantsHandoff() {
  try {
    return new URLSearchParams(window.location.search).get('handoff') === '1' || Boolean(window.opener);
  } catch {
    return false;
  }
}

export default function LegacySessionRoot() {
  const [profile, setProfile] = useState(() => sessionClient.getSnapshot());
  const [handoff, setHandoff] = useState(null);
  const [waiting, setWaiting] = useState(wantsHandoff);
  const [notice, setNotice] = useState(null);

  useEffect(() => sessionClient.subscribe((current) => {
    setProfile((previous) => {
      if (previous?.user?.id !== current?.user?.id) { clearFinanceStorage(); clearUploadBlobs(); }
      return current;
    });
  }), []);

  useEffect(() => {
    if (!waiting) return undefined;
    const origins = dashboardOrigins();
    const ping = () => {
      if (!window.opener) return;
      for (const origin of origins) {
        try { window.opener.postMessage({ type: 'accutax-widget-handoff-ready' }, origin); } catch { /* cross-origin until it posts */ }
      }
    };
    const onMessage = (event) => {
      if (!origins.includes(event.origin) || event.data?.type !== 'accutax-widget-handoff') return;
      if (event.data.token) sessionClient.useToken(event.data.token, event.data);
      setHandoff(event.data);
      setWaiting(false);
      event.source?.postMessage({ type: 'accutax-widget-handoff-ack' }, event.origin);
    };
    window.addEventListener('message', onMessage);
    ping();
    const pinger = window.setInterval(ping, 250);
    const timeout = window.setTimeout(() => {
      setWaiting(false);
      if (!sessionClient.getSnapshot()) setNotice('Could not continue from the dashboard. Sign in below.');
    }, HANDOFF_WAIT_MS);
    return () => {
      window.removeEventListener('message', onMessage);
      window.clearInterval(pinger);
      window.clearTimeout(timeout);
    };
  }, [waiting]);

  if (waiting && !profile) return <main style={{ padding: 40, textAlign: 'center' }} role="status">Continuing from the dashboard…</main>;
  if (!profile) return <LegacyLoginPage onLoginSuccess={() => setNotice(null)} notice={notice} />;
  return (
    <App key={profile.user.id} sessionProfile={profile} initialHandoff={handoff}
      onSessionLogout={() => sessionClient.logout()} />
  );
}
