import React, { useEffect, useMemo, useState } from 'react';
import { ShieldCheck, X, RefreshCw, Download } from 'lucide-react';
import { fetchPrivacySnapshots, fetchPrivacySnapshot, downloadPrivacySnapshot } from '../services/api';

// Privacy snapshots: proof that PII masking works, for admins (PII_SNAPSHOT_ADMINS).
// Each request shows four panes per model call: what the request held (real values, red), what the model was
// sent (placeholders, amber), what the model wrote back, and what the user saw.

const PURPOSE = { agent: 'Chat answer', agent_law: 'VAT law answer', report_narration: 'Report summary' };
const TOKEN_RX = /\\?\[\s*(SUPPLIER|CUSTOMER|PARTY|PERSON|ORG|TRN|EMAIL|PHONE|IBAN|ADDRESS|EMIRATES_ID|CARD|PASSPORT|GSTIN|PAN|AADHAAR|TAN|CIN|LLPIN|IFSC|UPI_ID|UTR|EWAY_BILL|IRN)[ _-]?\d+\s*\\?\]/gi;

const escapeRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// The text split into plain strings and <mark>s for the matches of `rx`.
const highlight = (text, rx, color) => {
  if (!text || !rx) return text || '';
  const out = [];
  let last = 0;
  rx.lastIndex = 0;
  let m;
  while ((m = rx.exec(text)) !== null) {
    if (m[0] === '') { rx.lastIndex += 1; continue; }
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<mark key={m.index} style={{ ...styles.mark, ...color }}>{m[0]}</mark>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
};

// A conversation as readable text: role, text, tools called and what they returned.
const conversationText = (side) => {
  if (!side) return null;
  const lines = [];
  if (side.system) lines.push('── SYSTEM ──', side.system, '');
  for (const m of side.messages || []) {
    if (!m || typeof m !== 'object') continue;
    lines.push(`── ${String(m.role || '').toUpperCase()} ──`);
    for (const b of m.content || []) {
      if (!b || typeof b !== 'object') continue;
      if (b.text) lines.push(String(b.text));
      if (b.toolUse) lines.push(`→ tool ${b.toolUse.name}: ${JSON.stringify(b.toolUse.input)}`);
      if (b.toolResult) {
        for (const part of b.toolResult.content || []) {
          if (part?.json !== undefined) lines.push(`← result: ${JSON.stringify(part.json, null, 1)}`);
          else if (part?.text) lines.push(`← result: ${part.text}`);
        }
      }
      if (b.document || b.image) lines.push('[attached file: sent as is]');
    }
    lines.push('');
  }
  return lines.join('\n');
};

const Pane = ({ title, note, children }) => (
  <div style={styles.pane}>
    <div style={styles.paneTitle}>{title}</div>
    <div style={styles.paneNote}>{note}</div>
    <pre style={styles.pre}>{children}</pre>
  </div>
);

const Verdict = ({ found }) => {
  if (found === null || found === undefined) return <span style={styles.pill}>Sentinel: not run</span>;
  const kinds = Object.entries(found);
  if (!kinds.length) return <span style={{ ...styles.pill, ...styles.ok }}>Sentinel: nothing personal sent</span>;
  return <span style={{ ...styles.pill, ...styles.bad }}>Sentinel found: {kinds.map(([k, v]) => `${v} ${k}`).join(', ')}</span>;
};

export const PrivacySnapshotsModal = ({ onClose, token = '', access = {} }) => {
  const [list, setList] = useState([]);
  const [selected, setSelected] = useState(null);
  const [snap, setSnap] = useState(null);
  const [callIndex, setCallIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadList = async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await fetchPrivacySnapshots(token);
      setList(items);
      if (items.length && !selected) setSelected(items[0].id);
    } catch (err) {
      setError(err.message || 'Could not load snapshots');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadList(); }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setSnap(null);
    setCallIndex(0);
    fetchPrivacySnapshot(selected, token)
      .then((s) => { if (live) setSnap(s); })
      .catch((err) => { if (live) setError(err.message || 'Could not load the snapshot'); });
    return () => { live = false; };
  }, [selected, token]);

  const valueRx = useMemo(() => {
    const values = Object.values(snap?.vault || {}).filter((v) => v && String(v).length >= 3)
      .sort((a, b) => b.length - a.length).map((v) => escapeRx(String(v)));
    return values.length ? new RegExp(values.join('|'), 'gi') : null;
  }, [snap]);

  const call = snap?.calls?.[callIndex];
  const hidden = snap?.stats?.hidden || {};
  const sentLabel = snap?.mode === 'on' ? '2 · Sent to the model' : '2 · Would be sent (shadow mode)';

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.titleGroup}>
            <ShieldCheck size={22} color="var(--accent)" />
            <div>
              <h3 style={{ margin: 0 }}>Privacy snapshots</h3>
              <p style={styles.subtitle}>
                What each request held, what the AI model received, and what the user saw. PII_MODE {access.mode || '?'}
                {access.snapshots ? '' : ' · snapshots are off (PII_SNAPSHOTS)'}
                {access.raw_kept ? ` · real values kept ${access.raw_days} days, encrypted` : ' · real values not kept (no key)'}
              </p>
            </div>
          </div>
          <div style={styles.headerRight}>
            <button className="btn btn-secondary" onClick={loadList} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'pulse-animation' : ''} /><span>Refresh</span>
            </button>
            {selected && (
              <button className="btn btn-secondary" onClick={() => downloadPrivacySnapshot(selected, token).catch((e) => setError(e.message))}>
                <Download size={14} /><span>Download</span>
              </button>
            )}
            <button style={styles.closeBtn} onClick={onClose} aria-label="Close"><X size={20} color="var(--ink-faint)" /></button>
          </div>
        </div>

        {error && <div style={styles.error}>{error}</div>}

        <div style={styles.body}>
          <div style={styles.list}>
            {!list.length && !loading && <div style={styles.empty}>No snapshots yet. Ask a question with PII_MODE and PII_SNAPSHOTS on.</div>}
            {list.map((item) => (
              <button key={item.id} onClick={() => setSelected(item.id)}
                style={{ ...styles.item, ...(item.id === selected ? styles.itemActive : {}) }}>
                <div style={styles.itemTop}>
                  <span>{(item.ts || '').replace('T', ' ').slice(0, 16)}</span>
                  {item.sentinel_pass === true && <span style={{ ...styles.dot, background: 'var(--success)' }} title="Nothing personal sent" />}
                  {item.sentinel_pass === false && <span style={{ ...styles.dot, background: 'var(--danger)' }} title="The sentinel found personal data" />}
                </div>
                <div style={styles.itemSub}>
                  user {item.user_id} · orgs {(item.organization_ids || []).join(', ')} · {item.hidden} hidden
                </div>
                <div style={styles.itemSub}>{(item.purposes || []).map((p) => PURPOSE[p] || p).join(' + ')}{item.mode === 'shadow' ? ' · shadow' : ''}</div>
              </button>
            ))}
          </div>

          <div style={styles.detail}>
            {!snap && selected && <div style={styles.empty}>Loading…</div>}
            {snap && (
              <>
                <div style={styles.summary}>
                  <span>Request {snap.id}</span>
                  <span>Hidden: {Object.entries(hidden).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(', ') || 'none'}</span>
                  <span>Unresolved placeholders: {snap.stats?.unresolved ?? 0}</span>
                  <span style={{ color: 'var(--ink-faint)' }}>Amounts, VAT, dates and document numbers stay visible by design.</span>
                </div>
                <div style={styles.tabs}>
                  {(snap.calls || []).map((c, i) => (
                    <button key={i} onClick={() => setCallIndex(i)}
                      style={{ ...styles.tab, ...(i === callIndex ? styles.tabActive : {}) }}>
                      {PURPOSE[c.purpose] || c.purpose}
                    </button>
                  ))}
                  {call && <Verdict found={call.sentinel} />}
                </div>
                {call && (
                  <div style={styles.panes}>
                    <Pane title="1 · What the request held" note="Real values (red), never sent. The model's own turns keep its placeholders.">
                      {snap.raw_available ? highlight(conversationText(call.raw), valueRx, styles.raw)
                        : 'Not kept: no encryption key, or past the retention period.'}
                    </Pane>
                    <Pane title={sentLabel} note="Placeholders (amber) in place of names and identifiers.">
                      {highlight(conversationText(call.sent), TOKEN_RX, styles.tok)}
                    </Pane>
                    <Pane title="3 · Model's reply" note="As the model wrote it.">
                      {highlight(String(call.reply_model || ''), TOKEN_RX, styles.tok)}
                    </Pane>
                    <Pane title="4 · What the user saw" note="Placeholders swapped back on our server.">
                      {snap.raw_available ? highlight(String(call.reply_user || ''), valueRx, styles.raw) : 'Not kept (real-value side).'}
                    </Pane>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

const styles = {
  modal: { maxWidth: '96vw', width: '1500px', height: '90vh', display: 'flex', flexDirection: 'column' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 12 },
  titleGroup: { display: 'flex', gap: 10, alignItems: 'flex-start' },
  subtitle: { margin: '2px 0 0', fontSize: 12, color: 'var(--ink-soft)' },
  headerRight: { display: 'flex', gap: 8, alignItems: 'center' },
  closeBtn: { background: 'none', border: 'none', cursor: 'pointer', padding: 4 },
  error: { background: 'var(--danger-soft)', color: 'var(--danger-ink)', padding: '8px 12px', borderRadius: 8, marginBottom: 8, fontSize: 13 },
  body: { display: 'flex', gap: 12, flex: 1, minHeight: 0 },
  list: { width: 260, flexShrink: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, borderRight: '1px solid var(--border)', paddingRight: 8 },
  item: { textAlign: 'left', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer', color: 'var(--ink)', font: 'inherit' },
  itemActive: { borderColor: 'var(--accent)', background: 'var(--accent-soft)' },
  itemTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13, fontWeight: 600 },
  itemSub: { fontSize: 11.5, color: 'var(--ink-soft)', marginTop: 2 },
  dot: { width: 8, height: 8, borderRadius: 99, display: 'inline-block' },
  detail: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 },
  summary: { display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12.5, color: 'var(--ink-soft)', marginBottom: 8 },
  tabs: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 },
  tab: { border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 99, padding: '4px 12px', cursor: 'pointer', fontSize: 12.5, color: 'var(--ink)' },
  tabActive: { background: 'var(--accent-soft)', borderColor: 'var(--accent)', color: 'var(--accent-ink)', fontWeight: 600 },
  panes: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10, flex: 1, minHeight: 0, overflowY: 'auto' },
  pane: { border: '1px solid var(--border)', borderRadius: 8, padding: 10, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 },
  paneTitle: { fontSize: 11.5, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--ink)' },
  paneNote: { fontSize: 11.5, color: 'var(--ink-soft)', margin: '2px 0 6px' },
  pre: { margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'var(--font-mono)', fontSize: 11.5, lineHeight: 1.5, background: 'var(--surface-2)', borderRadius: 6, padding: 8, overflow: 'auto', maxHeight: '62vh' },
  mark: { borderRadius: 3, padding: '0 2px' },
  tok: { background: 'var(--warning-soft)', color: 'var(--warning-ink)' },
  raw: { background: 'var(--danger-soft)', color: 'var(--danger-ink)' },
  pill: { fontSize: 12, fontWeight: 600, padding: '2px 10px', borderRadius: 99, background: 'var(--surface-2)', marginLeft: 'auto' },
  ok: { background: 'var(--success-soft)', color: 'var(--success-ink)' },
  bad: { background: 'var(--danger-soft)', color: 'var(--danger-ink)' },
  empty: { color: 'var(--ink-soft)', fontSize: 13, padding: 12 },
};
