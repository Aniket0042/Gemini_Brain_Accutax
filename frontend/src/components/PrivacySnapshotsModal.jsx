import React, { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ShieldCheck, X, RefreshCw, Download, Play } from 'lucide-react';
import { fetchPrivacySnapshots, fetchPrivacySnapshot, downloadPrivacySnapshot, runPrivacyDemo } from '../services/api';

// Privacy snapshots: proof that PII masking works, for admins (PII_SNAPSHOT_ADMINS).
// Each request shows four panes per model call: what the request held (real values, red), what the model was
// sent (placeholders, amber), what the model wrote back, and what the user saw.

const PURPOSE = { agent: 'Chat answer', agent_law: 'VAT law answer', report_narration: 'Report summary' };
const TOKEN_RX = /\\?\[\s*(SUPPLIER|CUSTOMER|PARTY|PERSON|ORG|TRN|EMAIL|PHONE|IBAN|ADDRESS|EMIRATES_ID|CARD|PASSPORT|GSTIN|PAN|AADHAAR|TAN|CIN|LLPIN|IFSC|UPI_ID|UTR|EWAY_BILL|IRN)[ _-]?\d+\s*\\?\]/gi;

const escapeRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// A snapshot's UTC timestamp in the viewer's own time zone.
const localTime = (ts) => {
  const d = ts ? new Date(ts) : null;
  return d && !Number.isNaN(d.getTime())
    ? d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : (ts || '');
};

// A model reply rendered as markdown, with placeholders (amber) or real values (red) marked. The marked parts go
// through as inline code so they keep their highlight inside tables and bold text.
const MarkedMarkdown = ({ text, rx, color }) => {
  const source = rx ? String(text || '').replace(rx, (m) => `\`${m}\``) : String(text || '');
  rx && (rx.lastIndex = 0);
  return (
    <div style={styles.md} className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        code: ({ inline, children, ...props }) => {
          const value = String(children);
          const marked = rx && new RegExp(`^(?:${rx.source})$`, rx.flags.replace('g', '')).test(value);
          return marked ? <mark style={{ ...styles.mark, ...color }}>{value}</mark> : <code {...props}>{children}</code>;
        },
      }}>{source}</ReactMarkdown>
    </div>
  );
};

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
const conversationText = (side, withSystem = false) => {
  if (!side) return null;
  const lines = [];
  if (withSystem && side.system) lines.push('── AI INSTRUCTIONS (SYSTEM PROMPT) ──', side.system, '');
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

const Pane = ({ title, note, children, rich = false }) => (
  <div style={styles.pane}>
    <div style={styles.paneTitle}>{title}</div>
    <div style={styles.paneNote}>{note}</div>
    {rich ? <div style={styles.rich}>{children}</div> : <pre style={styles.pre}>{children}</pre>}
  </div>
);

const Verdict = ({ found }) => {
  if (found === null || found === undefined) return <span style={styles.pill}>Sentinel: not run</span>;
  const kinds = Object.entries(found);
  if (!kinds.length) return <span style={{ ...styles.pill, ...styles.ok }}>Sentinel: nothing personal sent</span>;
  return <span style={{ ...styles.pill, ...styles.bad }}>Sentinel found: {kinds.map(([k, v]) => `${v} ${k}`).join(', ')}</span>;
};

// One request, both sides: a tab per model call and four panes per call. Used by the snapshot list and the demo.
const SnapshotDetail = ({ snap }) => {
  const [callIndex, setCallIndex] = useState(0);
  const [showSystem, setShowSystem] = useState(false);
  useEffect(() => { setCallIndex(0); }, [snap?.id]);

  const valueRx = useMemo(() => {
    const values = Object.values(snap?.vault || {}).filter((v) => v && String(v).length >= 3)
      .sort((a, b) => b.length - a.length).map((v) => escapeRx(String(v)));
    return values.length ? new RegExp(values.join('|'), 'gi') : null;
  }, [snap]);

  if (!snap) return null;
  const call = snap.calls?.[callIndex];
  const hidden = snap.stats?.hidden || {};
  const sentLabel = snap.mode === 'on' ? '2 · Sent to the model' : '2 · Would be sent (shadow mode)';
  return (
    <>
      <div style={styles.summary}>
        <span>{snap.demo ? 'Live demo' : 'Request'} {snap.id}</span>
        {snap.ts && <span>{localTime(snap.ts)}</span>}
        {snap.user_label && <span>{snap.user_label}</span>}
        {(snap.organization_names || []).length > 0 && <span>{snap.organization_names.join(', ')}</span>}
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
        <button style={styles.linkBtn} onClick={() => setShowSystem((v) => !v)}>
          {showSystem ? 'Hide AI instructions' : 'Show AI instructions'}
        </button>
        {call && <Verdict found={call.sentinel} />}
      </div>
      {call && (
        <div style={styles.panes}>
          <Pane title="1 · What the request held" note="The question, the data the AI asked for, real values in red. Never sent to the AI.">
            {snap.raw_available ? highlight(conversationText(call.raw, showSystem), valueRx, styles.raw)
              : 'Not kept: no encryption key, or past the retention period.'}
          </Pane>
          <Pane title={sentLabel} note="The same, with placeholders (amber) in place of names and identifiers.">
            {highlight(conversationText(call.sent, showSystem), TOKEN_RX, styles.tok)}
          </Pane>
          <Pane title="3 · Model's reply" note="As the model wrote it." rich>
            <MarkedMarkdown text={call.reply_model} rx={TOKEN_RX} color={styles.tok} />
          </Pane>
          <Pane title="4 · What the user saw" note="Placeholders swapped back on our server." rich>
            {snap.raw_available ? <MarkedMarkdown text={call.reply_user} rx={valueRx} color={styles.raw} />
              : 'Not kept (real-value side).'}
          </Pane>
        </div>
      )}
    </>
  );
};

const DEMO_EXAMPLES = [
  'Top 5 suppliers by purchases this year',
  'How much did we pay our biggest supplier this year? Their TRN is 100234567800003.',
  'Which customers owe us the most right now?',
];

// Live before/after: ask one question with masking on, then see what the AI got next to the real data.
const DemoPanel = ({ token, orgIds, orgNames = {} }) => {
  const [question, setQuestion] = useState(DEMO_EXAMPLES[0]);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    if (!question.trim() || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await runPrivacyDemo(question.trim(), orgIds || [], token));
    } catch (err) {
      setError(err.message || 'The demo could not run');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div style={styles.demo}>
      <div style={styles.demoBar}>
        <textarea id="privacy-demo-question" value={question} onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run(); }}
          rows={2} style={styles.demoInput} placeholder="Ask about suppliers, customers, TRNs…" />
        <button onClick={run} disabled={running || !question.trim()}
          style={{ ...styles.runBtn, ...(running || !question.trim() ? styles.runBtnOff : {}) }}>
          <Play size={14} /><span>{running ? 'Running…' : 'Run'}</span>
        </button>
      </div>
      <div style={styles.demoHint}>
        Runs on the organizations selected in the chat ({(orgIds || []).map((o) => orgNames[o] || `Organization ${o}`).join(', ') || 'none'}), with masking on.
        Try: {DEMO_EXAMPLES.map((q, i) => (
          <button key={i} style={styles.example} onClick={() => setQuestion(q)}>{q}</button>
        ))}
      </div>
      {error && <div style={styles.error}>{error}</div>}
      {running && <div style={styles.empty}>Asking the AI with masking on… this takes 5 to 20 seconds.</div>}
      {!running && !result && !error && (
        <div style={styles.demoEmpty}>
          <ShieldCheck size={28} color="var(--accent)" />
          <div style={styles.demoEmptyTitle}>Run a question to see both sides</div>
          <div>The four panes appear here: the real data, what the AI received (placeholders), the AI's reply, and the
            answer the user sees, with the sentinel's verdict on what was sent.</div>
        </div>
      )}
      {result && <SnapshotDetail snap={result} />}
    </div>
  );
};

export const PrivacySnapshotsModal = ({ onClose, token = '', access = {}, orgIds = [], orgNames = {} }) => {
  const [view, setView] = useState('snapshots');
  const [includeTest, setIncludeTest] = useState(false);
  const [list, setList] = useState([]);
  const [selected, setSelected] = useState(null);
  const [snap, setSnap] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadList = async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await fetchPrivacySnapshots(token, 100, includeTest);
      setList(items);
      if (items.length && !selected) setSelected(items[0].id);
    } catch (err) {
      setError(err.message || 'Could not load snapshots');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadList(); }, [token, includeTest]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setSnap(null);
    fetchPrivacySnapshot(selected, token)
      .then((s) => { if (live) setSnap(s); })
      .catch((err) => { if (live) setError(err.message || 'Could not load the snapshot'); });
    return () => { live = false; };
  }, [selected, token]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.titleGroup}>
            <ShieldCheck size={22} color="var(--accent)" />
            <div>
              <h3 style={{ margin: 0 }}>Privacy</h3>
              <p style={styles.subtitle}>
                What each request held, what the AI model received, and what the user saw. PII_MODE {access.mode || '?'}
                {access.snapshots ? '' : ' · snapshots are off (PII_SNAPSHOTS)'}
                {access.raw_kept ? ` · real values kept ${access.raw_days} days, encrypted` : ' · real values not kept (no key)'}
              </p>
            </div>
          </div>
          <div style={styles.headerRight}>
            <div style={styles.viewTabs} role="tablist">
              <button role="tab" aria-selected={view === 'snapshots'} onClick={() => setView('snapshots')}
                style={{ ...styles.tab, ...(view === 'snapshots' ? styles.tabActive : {}) }}>Snapshots</button>
              <button role="tab" aria-selected={view === 'demo'} onClick={() => setView('demo')}
                style={{ ...styles.tab, ...(view === 'demo' ? styles.tabActive : {}) }}>Live demo</button>
            </div>
            {view === 'snapshots' && (
              <>
                <button className="btn btn-secondary" onClick={loadList} disabled={loading}>
                  <RefreshCw size={14} className={loading ? 'pulse-animation' : ''} /><span>Refresh</span>
                </button>
                {selected && (
                  <button className="btn btn-secondary" onClick={() => downloadPrivacySnapshot(selected, token).catch((e) => setError(e.message))}>
                    <Download size={14} /><span>Download</span>
                  </button>
                )}
              </>
            )}
            <button style={styles.closeBtn} onClick={onClose} aria-label="Close"><X size={20} color="var(--ink-faint)" /></button>
          </div>
        </div>

        {view === 'demo' ? (
          <DemoPanel token={token} orgIds={orgIds} orgNames={orgNames} />
        ) : (
          <>
            {error && <div style={styles.error}>{error}</div>}
            <div style={styles.body}>
              <div style={styles.list}>
                <label style={styles.filter}>
                  <input id="privacy-include-test" type="checkbox" checked={includeTest}
                    onChange={(e) => setIncludeTest(e.target.checked)} />
                  Show test runs
                </label>
                {!list.length && !loading && <div style={styles.empty}>No snapshots yet. Ask a question with PII_MODE and PII_SNAPSHOTS on.</div>}
                {list.map((item) => (
                  <button key={item.id} onClick={() => setSelected(item.id)}
                    style={{ ...styles.item, ...(item.id === selected ? styles.itemActive : {}) }}>
                    <div style={styles.itemTop}>
                      <span>{localTime(item.ts)}</span>
                      {item.sentinel_pass === true && <span style={{ ...styles.dot, background: 'var(--success)' }} title="Nothing personal sent" />}
                      {item.sentinel_pass === false && <span style={{ ...styles.dot, background: 'var(--danger)' }} title="The sentinel found personal data" />}
                    </div>
                    <div style={styles.itemSub}>{item.user_label || `user ${item.user_id}`} · {item.hidden} hidden</div>
                    <div style={styles.itemSub} title={(item.organization_names || []).join(', ')}>
                      {(item.organization_names || item.organization_ids || []).join(', ')}
                    </div>
                    <div style={styles.itemSub}>{(item.purposes || []).map((p) => PURPOSE[p] || p).join(' + ')}{item.mode === 'shadow' ? ' · shadow' : ''}</div>
                  </button>
                ))}
              </div>
              <div style={styles.detail}>
                {!snap && selected && <div style={styles.empty}>Loading…</div>}
                <SnapshotDetail snap={snap} />
              </div>
            </div>
          </>
        )}
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
  viewTabs: { display: 'flex', gap: 4, marginRight: 8 },
  linkBtn: { background: 'none', border: 'none', color: 'var(--link)', cursor: 'pointer', fontSize: 12.5, padding: '0 4px' },
  rich: { fontSize: 12.5, lineHeight: 1.55, background: 'var(--surface-2)', borderRadius: 6, padding: '8px 10px', overflow: 'auto', maxHeight: '62vh', color: 'var(--ink)' },
  md: { wordBreak: 'break-word' },
  runBtn: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '0 18px', borderRadius: 8, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 14, background: 'var(--accent)', color: 'var(--surface)' },
  runBtnOff: { opacity: 0.45, cursor: 'not-allowed' },
  demoEmpty: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, flex: 1, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 13, padding: 24, maxWidth: 560, margin: '0 auto' },
  demoEmptyTitle: { fontSize: 15, fontWeight: 600, color: 'var(--ink)' },
  filter: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--ink-soft)', padding: '2px 2px 6px' },
  demo: { display: 'flex', flexDirection: 'column', gap: 8, flex: 1, minHeight: 0 },
  demoBar: { display: 'flex', gap: 8, alignItems: 'stretch' },
  demoInput: { flex: 1, resize: 'vertical', font: 'inherit', fontSize: 14, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--ink)' },
  demoHint: { fontSize: 12, color: 'var(--ink-soft)', display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
  example: { border: '1px dashed var(--border-strong)', background: 'none', borderRadius: 99, padding: '2px 10px', fontSize: 12, color: 'var(--ink-soft)', cursor: 'pointer' },
};
