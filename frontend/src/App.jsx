import React, { useState, useEffect, useRef, useMemo } from 'react';
import { LoginPage } from './components/LoginPage';
import { QueryInput } from './components/QueryInput';
import { ResponseView } from './components/ResponseView';
import { TenantLoginModal } from './components/TenantLoginModal';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { IntroSuggestions } from './components/IntroSuggestions';
import { ModelHealthModal } from './components/ModelHealthModal';
import { AnswerCanvas } from './components/canvas/AnswerCanvas';
import { fetchQueryResponse, streamQueryResponse, fetchAllModelsResponse, fetchTenants, fetchModelCatalog, fetchSessionMessages, createChatSession, deleteChatSession } from './services/api';
import { Trash2 } from 'lucide-react';
import { useAuth0 } from '@auth0/auth0-react';

// Minimal fallback shown only until /api/v1/tenants responds. Deliberately
// carries no figures: the previous version described specific invoice counts
// and AED totals for a dataset this deployment is not necessarily connected
// to, which read as real data while being unverifiable.
function extractCanvasPayload(conversation) {
  for (let i = (conversation || []).length - 1; i >= 0; i -= 1) {
    const msg = conversation[i];
    if (msg.role !== 'assistant' || msg.isStreaming) continue;
    const blocks = msg.responseData?.blocks;
    if (!Array.isArray(blocks)) continue;
    const canvas = blocks.find((b) => b.type === 'canvas');
    const artifact = blocks.find((b) => b.type === 'artifact');
    if (canvas || artifact) {
      const artifacts = blocks.filter((b) => b.type === 'artifact');
      return { spec: canvas?.spec || null, artifact: artifact || artifacts[0] || null, artifacts };
    }
  }
  return null;
}

const DEFAULT_ORGANIZATIONS = [];

function mapWidgetConversation(turns = []) {
  return turns.map((msg) => {
    if (msg.role === 'user') {
      return { role: 'user', content: msg.content, timestamp: msg.timestamp || Date.now() };
    }
    return {
      role: 'assistant',
      isStreaming: false,
      streamingText: msg.content || '',
      tableData: msg.tableMarkdown || '',
      responseData: {
        answer: msg.content || '',
        table_markdown: msg.tableMarkdown || '',
      },
      timestamp: msg.timestamp || Date.now(),
    };
  });
}

function mapApiTranscript(messages = []) {
  return messages.map((msg) => {
    if (msg.role === 'user') {
      return { role: 'user', content: msg.content, timestamp: msg.created_at ? Date.parse(msg.created_at) : Date.now() };
    }
    const blocks = Array.isArray(msg.blocks) ? msg.blocks : [];
    return {
      role: 'assistant',
      isStreaming: false,
      streamingText: msg.content || '',
      tableData: '',
      responseData: {
        answer: msg.content || '',
        table_markdown: '',
        blocks,
      },
      timestamp: msg.created_at ? Date.parse(msg.created_at) : Date.now(),
    };
  });
}

const DELETED_SESSIONS_KEY = 'accutax_deleted_sessions';
const ACTIVE_SESSION_KEY = 'accutax_active_session';
const CANVAS_WIDTH_KEY = 'accutax_canvas_width';
const CANVAS_WIDTH_MIN = 360;
const CANVAS_WIDTH_MAX = 920;
const CANVAS_WIDTH_DEFAULT = 480;
const SENSITIVE_QUERY_KEYS = ['orgId', 'sessionId', 'currentPage', 'activeYear', 'contactName', 'bankAccount'];

function readCanvasWidth() {
  try {
    const n = Number(localStorage.getItem(CANVAS_WIDTH_KEY));
    if (Number.isFinite(n) && n >= CANVAS_WIDTH_MIN && n <= CANVAS_WIDTH_MAX) return n;
  } catch {
    /* ignore */
  }
  return CANVAS_WIDTH_DEFAULT;
}

function readDeletedSessions() {
  try {
    const raw = sessionStorage.getItem(DELETED_SESSIONS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function rememberDeletedSession(sessionId) {
  if (!sessionId) return;
  const next = readDeletedSessions();
  next.add(sessionId);
  try {
    sessionStorage.setItem(DELETED_SESSIONS_KEY, JSON.stringify([...next]));
  } catch {
    /* ignore quota */
  }
}

function allowedDashboardOrigin() {
  try {
    return new URL(import.meta.env.VITE_ACCUTAX_APP_URL || 'http://localhost:5173').origin;
  } catch {
    return 'http://localhost:5173';
  }
}

function originAliases(origin) {
  try {
    const url = new URL(origin);
    const hosts = new Set([url.hostname]);
    if (url.hostname === 'localhost') hosts.add('127.0.0.1');
    if (url.hostname === '127.0.0.1') hosts.add('localhost');
    const port = url.port ? `:${url.port}` : '';
    return [...hosts].map((host) => `${url.protocol}//${host}${port}`);
  } catch {
    return [origin];
  }
}

function isAllowedDashboardOrigin(origin) {
  return originAliases(allowedDashboardOrigin()).includes(origin);
}

function pingOpenerReady() {
  if (!window.opener) return;
  for (const origin of originAliases(allowedDashboardOrigin())) {
    try {
      window.opener.postMessage({ type: 'accutax-widget-handoff-ready' }, origin);
    } catch {
      /* opener may be cross-origin until it posts first */
    }
  }
}

function sanitizeSensitiveQueryParams({ includeHandoff = false } = {}) {
  try {
    const url = new URL(window.location.href);
    const keys = includeHandoff ? [...SENSITIVE_QUERY_KEYS, 'handoff'] : SENSITIVE_QUERY_KEYS;
    let changed = false;
    for (const key of keys) {
      if (url.searchParams.has(key)) {
        url.searchParams.delete(key);
        changed = true;
      }
    }
    if (!changed) return;
    const qs = url.searchParams.toString();
    window.history.replaceState({}, '', `${url.pathname}${qs ? `?${qs}` : ''}${url.hash}`);
  } catch {
    /* ignore */
  }
}

function stripSessionIdFromUrl() {
  sanitizeSensitiveQueryParams({ includeHandoff: true });
}

export default function App() {
  // Current Authenticated User & Token State
  const [currentUser, setCurrentUser] = useState(null);

  // Active Tenant Context State
  const [activeTenant, setActiveTenant] = useState(null);
  const [availableTenants, setAvailableTenants] = useState(DEFAULT_ORGANIZATIONS);

  // UI Modals State
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showHealthModal, setShowHealthModal] = useState(false);

  // Execution & Streaming State
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(true);
  const [responseData, setResponseData] = useState(null);
  const [streamLogs, setStreamLogs] = useState([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // External UI context from the dashboard widget (postMessage), never the URL.
  const [uiContext, setUiContext] = useState(null);

  // Theme: 'system' follows the OS, 'light'/'dark' are explicit overrides.
  // Stamped onto <html> as data-theme so index.css's two dark-mode blocks
  // both key off the same attribute the toggle in Header.jsx writes.
  const [theme, setTheme] = useState(() => localStorage.getItem('accutax_theme') || 'system');

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', theme);
    }
    localStorage.setItem('accutax_theme', theme);
  }, [theme]);

  const cycleTheme = () => {
    setTheme((prev) => (prev === 'system' ? 'light' : prev === 'light' ? 'dark' : 'system'));
  };

  // Model selection. Defaults to auto — the backend policy router picks, and
  // explains its choice in the response. Effort is no longer user-selectable:
  // every query runs at the highest tier its model supports (backend default).
  const [modelCatalog, setModelCatalog] = useState(null);
  // 'loading' | 'ready' | 'error' — drives what the picker says when the
  // catalog endpoint is unreachable, instead of silently showing only "Auto".
  const [catalogState, setCatalogState] = useState('loading');
  const [selectedModel, setSelectedModel] = useState(() => localStorage.getItem('accutax_model') || 'auto');
  const [brief, setBrief] = useState(() => localStorage.getItem('accutax_brief') === '1');
  const [catalogReloadKey, setCatalogReloadKey] = useState(0);

  // Conversation History List state
  const [conversation, setConversation] = useState([]);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasSpec, setCanvasSpec] = useState(null);
  const [canvasArtifact, setCanvasArtifact] = useState(null);
  const [canvasArtifacts, setCanvasArtifacts] = useState([]);
  const [canvasWidth, setCanvasWidth] = useState(readCanvasWidth);

  useEffect(() => {
    try {
      localStorage.setItem(CANVAS_WIDTH_KEY, String(canvasWidth));
    } catch {
      /* ignore */
    }
  }, [canvasWidth]);
  const [widgetSessionId, setWidgetSessionId] = useState(() => {
    try {
      // Fresh widget handoff supplies the id via postMessage. Do not seed from
      // the URL (it is no longer a capability) and skip the last local id so
      // we do not flash the previous thread first.
      if (new URLSearchParams(window.location.search).get('handoff') === '1') return null;
      const id = sessionStorage.getItem(ACTIVE_SESSION_KEY);
      if (id && readDeletedSessions().has(id)) return null;
      return id || null;
    } catch {
      return null;
    }
  });
  const pendingHandoffRef = useRef(null);
  const handoffAppliedRef = useRef(false);
  const lastHandoffSessionRef = useRef(null);
  const lastCanvasKeyRef = useRef(null);
  const accutaxSsoAppliedRef = useRef(false);
  const [wantsAccutaxHandoff] = useState(() => {
    try {
      if (new URLSearchParams(window.location.search).get('handoff') === '1') return true;
      if (sessionStorage.getItem('accutax_sso_token')) return true;
      if (window.opener) return true;
      return false;
    } catch {
      return false;
    }
  });
  const [handoffTimedOut, setHandoffTimedOut] = useState(false);
  const [handoffSettled, setHandoffSettled] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).get('handoff') !== '1';
    } catch {
      return true;
    }
  });

  // Persisted session history for sidebar — always sorted most-recently-active
  // first, so display order matches storage order regardless of how the
  // underlying array happened to be written.
  const [chatHistory, setChatHistory] = useState(() => {
    try {
      const saved = localStorage.getItem('accutax_chat_history');
      const parsed = saved ? JSON.parse(saved) : [];
      return parsed.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    } catch { return []; }
  });

  // Which saved history entry (if any) the current conversation was loaded
  // from / already belongs to — lets us update that entry in place instead
  // of creating a duplicate "postcard" every time the session is put away.
  const [activeHistoryId, setActiveHistoryId] = useState(null);

  // True once the open conversation has real, unsaved activity (a message
  // sent) since it was loaded/created. Merely opening a saved session to look
  // at it must NOT touch its timestamp or reorder the list — only actually
  // talking in it should, exactly like every other chat app's session list.
  const [historyDirty, setHistoryDirty] = useState(false);
  const chatHistoryRef = useRef(chatHistory);
  chatHistoryRef.current = chatHistory;

  useEffect(() => {
    try {
      if (widgetSessionId) sessionStorage.setItem(ACTIVE_SESSION_KEY, widgetSessionId);
      else sessionStorage.removeItem(ACTIVE_SESSION_KEY);
    } catch {
      /* ignore */
    }
  }, [widgetSessionId]);

  useEffect(() => {
    sanitizeSensitiveQueryParams({ includeHandoff: false });
  }, []);

  useEffect(() => {
    const payload = extractCanvasPayload(conversation);
    if (!payload) return;
    const key = `${payload.spec?.generated_at || ''}:${payload.artifact?.id || payload.spec?.title || ''}`;
    if (key === lastCanvasKeyRef.current) return;
    lastCanvasKeyRef.current = key;
    setCanvasSpec(payload.spec);
    setCanvasArtifact(payload.artifact);
    setCanvasArtifacts(payload.artifacts || []);
    // Charts stay in the chat card. The canvas column opens only from Preview.
  }, [conversation]);

  // Auto-scroll target ref
  const scrollBottomRef = useRef(null);

  const { isAuthenticated, isLoading: authLoading, getAccessTokenSilently, loginWithRedirect, user: auth0User } = useAuth0();

  const applyAccutaxSession = (token, extra = {}) => {
    if (!token || accutaxSsoAppliedRef.current) return;
    accutaxSsoAppliedRef.current = true;
    const userObj = {
      access_token: token,
      email: extra.email || '',
      name: extra.name || extra.email || 'Accutax user',
      source: 'accutax',
    };
    try {
      sessionStorage.setItem('accutax_sso_token', token);
    } catch {
      /* ignore quota */
    }
    setCurrentUser(userObj);
    loadUserTenants(token, userObj);
  };

  // Continue-in-full-chat: reuse the Accutax JWT. Do not send the user to Auth0.
  useEffect(() => {
    if (!wantsAccutaxHandoff) return undefined;
    try {
      const cached = sessionStorage.getItem('accutax_widget_handoff');
      const parsed = cached ? JSON.parse(cached) : null;
      const token = parsed?.token || sessionStorage.getItem('accutax_sso_token');
      if (token) applyAccutaxSession(token, parsed || {});
    } catch {
      /* ignore */
    }
    return undefined;
  }, []);

  useEffect(() => {
    if (!wantsAccutaxHandoff || currentUser) return undefined;
    const timer = window.setTimeout(() => {
      setHandoffTimedOut(true);
      setHandoffSettled(true);
    }, 15000);
    return () => window.clearTimeout(timer);
  }, [wantsAccutaxHandoff, currentUser]);

  // Auth0 Silent Authentication — skipped when arriving from the Accutax widget
  useEffect(() => {
    if (accutaxSsoAppliedRef.current || currentUser?.source === 'accutax') return;
    if (wantsAccutaxHandoff && !handoffTimedOut) return;
    if (authLoading) return;
    if (!isAuthenticated) {
      loginWithRedirect();
      return;
    }
    const initAuth = async () => {
      try {
        const token = await getAccessTokenSilently();
        if (accutaxSsoAppliedRef.current) return;
        const userObj = { ...auth0User, access_token: token };
        setCurrentUser(userObj);
        loadUserTenants(token, userObj);
      } catch (e) {
        console.error('Failed to silently acquire Auth0 token:', e);
        if (!accutaxSsoAppliedRef.current) loginWithRedirect();
      }
    };
    initAuth();
  }, [authLoading, isAuthenticated, getAccessTokenSilently, loginWithRedirect, auth0User, currentUser, handoffTimedOut, wantsAccutaxHandoff]);

  const notifyWidgetSessionDeleted = (sessionId) => {
    if (!sessionId) return;
    try {
      sessionStorage.removeItem('accutax_widget_handoff');
    } catch {
      /* ignore */
    }
    const payload = { type: 'accutax-session-deleted', sessionId };
    for (const origin of originAliases(allowedDashboardOrigin())) {
      try {
        window.opener?.postMessage(payload, origin);
      } catch {
        /* opener may be gone */
      }
    }
  };

  const applyWidgetHandoff = (data) => {
    if (!data) return;
    if (data.token) applyAccutaxSession(data.token, data);
    const incomingSessionId = data.sessionId || null;
    if (incomingSessionId && readDeletedSessions().has(incomingSessionId)) {
      handoffAppliedRef.current = true;
      setHandoffSettled(true);
      sanitizeSensitiveQueryParams({ includeHandoff: true });
      return;
    }
    const alreadyThisSession =
      handoffAppliedRef.current &&
      incomingSessionId &&
      incomingSessionId === lastHandoffSessionRef.current;
    if (alreadyThisSession) {
      sanitizeSensitiveQueryParams({ includeHandoff: true });
      return;
    }
    if (incomingSessionId) setWidgetSessionId(incomingSessionId);
    lastHandoffSessionRef.current = incomingSessionId;
    if (data.orgId) {
      localStorage.setItem('gemini_brain_active_org', String(data.orgId));
    }
    if (data.currentPage || data.activeYear || data.contactName || data.bankAccount) {
      setUiContext({
        active_year: data.activeYear || null,
        current_page: data.currentPage || null,
        contact_name: data.contactName || null,
        bank_account: data.bankAccount || null,
      });
    }
    const mapped = mapWidgetConversation(data.conversation || []);
    handoffAppliedRef.current = true;
    setHandoffSettled(true);
    sanitizeSensitiveQueryParams({ includeHandoff: true });
    if (mapped.length === 0) {
      return;
    }
    try {
      sessionStorage.removeItem('accutax_widget_handoff');
    } catch {
      /* ignore */
    }
    setConversation(mapped);
    setHistoryDirty(true);
    const firstUserMsg = mapped.find((m) => m.role === 'user');
    const lastAssistant = [...mapped].reverse().find((m) => m.role === 'assistant');
    setChatHistory((prev) => {
      const existingIdx = incomingSessionId
        ? prev.findIndex((e) => e.sessionId === incomingSessionId)
        : -1;
      const entryId = existingIdx !== -1 ? prev[existingIdx].id : Date.now();
      setActiveHistoryId(entryId);
      const entryData = {
        id: entryId,
        sessionId: incomingSessionId,
        title: (firstUserMsg?.content || 'Widget chat').slice(0, 50),
        preview: (lastAssistant?.streamingText || '').slice(0, 60) || 'Continued from dashboard',
        timestamp: new Date().toISOString(),
        conversation: mapped,
        organizationId: data.orgId ?? null,
      };
      const withEntry = existingIdx !== -1
        ? prev.map((e, i) => (i === existingIdx ? { ...e, ...entryData } : e))
        : [entryData, ...prev];
      const updated = withEntry
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 30);
      localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      return updated;
    });
  };

  useEffect(() => {
    if (!wantsAccutaxHandoff) return undefined;

    try {
      const cached = sessionStorage.getItem('accutax_widget_handoff');
      if (cached) applyWidgetHandoff(JSON.parse(cached));
    } catch {
      /* ignore */
    }

    const onMsg = (event) => {
      if (!isAllowedDashboardOrigin(event.origin)) return;
      if (event.data?.type !== 'accutax-widget-handoff') return;
      pendingHandoffRef.current = event.data;
      try {
        sessionStorage.setItem('accutax_widget_handoff', JSON.stringify(event.data));
      } catch {
        /* ignore quota */
      }
      applyWidgetHandoff(event.data);
      event.source?.postMessage({ type: 'accutax-widget-handoff-ack' }, event.origin);
    };

    window.addEventListener('message', onMsg);
    pingOpenerReady();
    const readyTimer = window.setInterval(pingOpenerReady, 250);
    window.setTimeout(() => window.clearInterval(readyTimer), 15000);
    return () => {
      window.removeEventListener('message', onMsg);
      window.clearInterval(readyTimer);
    };
  }, [wantsAccutaxHandoff]);

  useEffect(() => {
    if (pendingHandoffRef.current && !handoffAppliedRef.current) {
      applyWidgetHandoff(pendingHandoffRef.current);
    }
  }, [currentUser]);

  useEffect(() => {
    if (widgetSessionId) return undefined;
    if (!handoffSettled) return undefined;
    const minted =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `chat-${Date.now()}`;
    setWidgetSessionId(minted);
    return undefined;
  }, [widgetSessionId, handoffSettled]);

  // Create the session row first, then load messages. Fetching a brand-new
  // UUID before POST finishes 404s; treating that 404 as "mint another id"
  // produced an unbounded create/404 loop.
  useEffect(() => {
    if (!widgetSessionId || !currentUser?.access_token || !activeTenant?.organization_id) {
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        if (readDeletedSessions().has(widgetSessionId)) {
          stripSessionIdFromUrl();
          setWidgetSessionId(null);
          return;
        }
        await createChatSession(
          activeTenant.organization_id,
          currentUser.access_token,
          widgetSessionId,
        );
        if (cancelled) return;
        const data = await fetchSessionMessages(widgetSessionId, currentUser.access_token);
        if (cancelled) return;
        const mapped = mapApiTranscript(data.messages || []);
        if (mapped.length === 0) return;
        const inHistory = chatHistoryRef.current.some(
          (e) => e.sessionId && e.sessionId === widgetSessionId,
        );
        // Stale thread after this browser deleted it. Do not resurrect from
        // sessionStorage. Fresh widget handoff sets handoffAppliedRef first.
        if (!inHistory && !handoffAppliedRef.current) return;
        handoffAppliedRef.current = true;
        setConversation(mapped);
        setHistoryDirty(false);
        const match = chatHistoryRef.current.find(
          (e) => e.sessionId && e.sessionId === widgetSessionId,
        );
        if (match) setActiveHistoryId(match.id);
      } catch (err) {
        if (!cancelled) {
          console.warn('Could not restore session transcript:', err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [widgetSessionId, currentUser?.access_token, activeTenant?.organization_id]);

  // Older sidebar entries had no org stamp. Attach them to the org that is
  // selected when they are first seen so they do not leak across tenants.
  useEffect(() => {
    const orgId = activeTenant?.organization_id ?? activeTenant?.id;
    if (orgId == null || orgId === '') return undefined;
    setChatHistory((prev) => {
      if (!prev.some((e) => e.organizationId == null)) return prev;
      const updated = prev.map((e) =>
        e.organizationId != null ? e : { ...e, organizationId: orgId },
      );
      localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      return updated;
    });
    return undefined;
  }, [activeTenant?.organization_id, activeTenant?.id]);

  // Smooth auto-scroll to bottom when new response or streaming status arrives
  useEffect(() => {
    if (responseData || isLoading || streamLogs.length > 0 || conversation.length > 0) {
      scrollBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [responseData, isLoading, streamLogs, conversation]);

  // Load accessible tenants from API
  const loadUserTenants = async (token, user) => {
    try {
      const data = await fetchTenants(token);
      if (data && data.tenants && data.tenants.length > 0) {
        setAvailableTenants(data.tenants);
        const savedOrgId = localStorage.getItem('gemini_brain_active_org');
        const matched = data.tenants.find((t) => String(t.id || t.organization_id) === String(savedOrgId)) || data.tenants[0];
        setActiveTenant({
          ...matched,
          organization_id: matched.id || matched.organization_id,
        });
        return;
      }
    } catch (e) {
      console.warn('Could not load tenants from API, using default list:', e);
    }
    initTenantFromUser(user);
  };

  // Helper to initialize active tenant from logged in user claims
  const initTenantFromUser = (user) => {
    const allowed = user?.allowed_org_ids || [];
    const savedOrgId = localStorage.getItem('gemini_brain_active_org');
    let selected = null;
    if (savedOrgId) {
      selected = DEFAULT_ORGANIZATIONS.find((o) => String(o.id) === String(savedOrgId));
    }
    if (!selected) {
      const firstOrgId = allowed.length > 0 ? allowed[0] : null;
      selected =
        DEFAULT_ORGANIZATIONS.find((o) => o.id === firstOrgId) ||
        (firstOrgId != null ? { id: firstOrgId, display_name: `Organization ${firstOrgId}` } : null);
    }
    if (!selected) {
      console.warn('No organization available for this user.');
      return;
    }
    setActiveTenant({
      ...selected,
      organization_id: selected.id || selected.organization_id,
    });
  };

  // Handle Switch Tenant from Dropdown
  const handleSelectTenant = (tenantObj) => {
    const orgId = tenantObj.id || tenantObj.organization_id;
    if (String(orgId) === String(activeTenant?.organization_id || activeTenant?.id)) return;
    persistCurrentConversation();
    const previousOrgId = activeTenant?.organization_id ?? activeTenant?.id;
    if (previousOrgId != null) {
      setChatHistory((prev) => {
        const updated = prev.map((e) =>
          e.organizationId != null ? e : { ...e, organizationId: previousOrgId },
        );
        localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
        return updated;
      });
    }
    const fullTenant = {
      ...tenantObj,
      organization_id: orgId,
    };
    setActiveTenant(fullTenant);
    localStorage.setItem('gemini_brain_active_org', String(orgId));
    setConversation([]);
    setActiveHistoryId(null);
    setHistoryDirty(false);
    setWidgetSessionId(null);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
  };

  // Handle successful login
  const handleLoginSuccess = (authResponse) => {
    const userSession = {
      user_id: authResponse.user_id,
      email: authResponse.email,
      access_token: authResponse.access_token,
      allowed_org_ids: authResponse.allowed_org_ids || [],
    };

    localStorage.setItem('gemini_brain_user', JSON.stringify(userSession));
    setCurrentUser(userSession);

    if (authResponse.tenants && authResponse.tenants.length > 0) {
      setAvailableTenants(authResponse.tenants);
      const firstT = authResponse.tenants[0];
      const fullT = {
        ...firstT,
        organization_id: firstT.id || firstT.organization_id,
        user_id: userSession.user_id,
      };
      setActiveTenant(fullT);
      localStorage.setItem('gemini_brain_active_org', String(fullT.organization_id));
    } else {
      initTenantFromUser(userSession);
    }
  };

  // Handle Logout
  const handleLogout = () => {
    localStorage.removeItem('gemini_brain_user');
    try {
      sessionStorage.removeItem('accutax_sso_token');
      sessionStorage.removeItem('accutax_widget_handoff');
    } catch {
      /* ignore */
    }
    accutaxSsoAppliedRef.current = false;
    setCurrentUser(null);
    setActiveTenant(null);
    setResponseData(null);
    setStreamLogs([]);
    setConversation([]);
    setActiveHistoryId(null);
  };

  // Persist the current conversation into chatHistory — either updating the
  // entry it was loaded from (activeHistoryId) or creating a new one. Pulled
  // out of handleClearConversation so it can also run before switching to a
  // different saved session, so in-progress work is never silently dropped.
  //
  // No-ops unless historyDirty — just opening a saved session to read it
  // must not touch its timestamp or shuffle the list. The whole list is
  // always re-sorted by timestamp (never spliced to the front by hand), so
  // ordering is purely "most recently active first" — same as any normal
  // chat app's session list.
  const persistCurrentConversation = () => {
    if (!historyDirty || conversation.length === 0) return;
    const firstUserMsg = conversation.find(m => m.role === 'user');
    if (!firstUserMsg) return;

    const lastAssistant = [...conversation].reverse().find(m => m.role === 'assistant');
    const preview = lastAssistant?.streamingText?.slice(0, 60) || lastAssistant?.responseData?.answer?.slice(0, 60) || 'Processing...';
    const entryData = {
      title: firstUserMsg.content.slice(0, 50),
      preview,
      timestamp: new Date().toISOString(),
      conversation, // full transcript, not just a title/preview snippet
      sessionId: widgetSessionId || null,
      organizationId: activeTenant?.organization_id ?? activeTenant?.id ?? null,
    };

    const nextId = activeHistoryId || Date.now();
    setChatHistory(prev => {
      const existingIdx = prev.findIndex((e) => e.id === nextId);
      const withEntry = existingIdx !== -1
        ? prev.map((e, i) => (i === existingIdx ? { ...e, ...entryData } : e))
        : [{ id: nextId, ...entryData }, ...prev];
      const updated = withEntry
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 30); // keep max 30
      localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      return updated;
    });
    if (!activeHistoryId) setActiveHistoryId(nextId);
    setHistoryDirty(false);
  };

  // Handle Clear Conversation (saves current session to history first)
  const handleClearConversation = () => {
    persistCurrentConversation();
    setConversation([]);
    setActiveHistoryId(null);
    setHistoryDirty(false);
    stripSessionIdFromUrl();
    setWidgetSessionId(null);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
    setCanvasOpen(false);
    setCanvasSpec(null);
    setCanvasArtifact(null);
    setCanvasArtifacts([]);
    lastCanvasKeyRef.current = null;
  };

  // Handle selecting a saved session from the sidebar history list — saves
  // whatever is currently open first, then swaps in the stored transcript.
  const handleSelectHistory = (id) => {
    if (id === activeHistoryId) return;
    persistCurrentConversation();
    const entry = chatHistory.find(e => e.id === id);
    if (!entry) return;
    const orgId = activeTenant?.organization_id ?? activeTenant?.id;
    if (entry.organizationId != null && String(entry.organizationId) !== String(orgId)) {
      return;
    }
    setConversation(entry.conversation || []);
    setActiveHistoryId(id);
    setHistoryDirty(false);
    if (entry.sessionId) setWidgetSessionId(entry.sessionId);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
  };

  // Handle deleting a saved session — removes it from state and localStorage.
  // If it's the one currently open, clear the active conversation too so a
  // deleted session doesn't keep sitting on screen (and can't be re-saved by
  // a later "New Session" click).
  const handleDeleteHistory = async (id) => {
    const entry = chatHistory.find((e) => e.id === id);
    const openFirst = conversation.find((m) => m.role === 'user')?.content;
    const deletedFirst = (entry?.conversation || []).find((m) => m.role === 'user')?.content;
    const deletedIsOpen =
      (activeHistoryId != null && String(id) === String(activeHistoryId)) ||
      (entry?.sessionId && widgetSessionId && entry.sessionId === widgetSessionId) ||
      Boolean(openFirst && deletedFirst && openFirst === deletedFirst);

    const sessionId =
      entry?.sessionId || (deletedIsOpen ? widgetSessionId : null);

    if (sessionId && currentUser?.access_token) {
      try {
        await deleteChatSession(sessionId, currentUser.access_token);
      } catch (err) {
        console.warn('Could not delete chat session on server:', err);
      }
    }

    rememberDeletedSession(sessionId);
    stripSessionIdFromUrl();
    notifyWidgetSessionDeleted(sessionId);
    handoffAppliedRef.current = false;
    if (sessionId && sessionId === lastHandoffSessionRef.current) {
      lastHandoffSessionRef.current = null;
    }

    setChatHistory((prev) => {
      const updated = prev.filter((e) => e.id !== id);
      localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      return updated;
    });

    if (deletedIsOpen) {
      setConversation([]);
      setActiveHistoryId(null);
      setHistoryDirty(false);
      setWidgetSessionId(null);
      setResponseData(null);
      setStreamLogs([]);
      setIsLoading(false);
    }
  };

  // Handle Query Submission
  // Load the model/effort catalog once authenticated. The picker falls back to
  // "Auto" alone if this fails, so a catalog outage never blocks asking a question.
  useEffect(() => {
    const token = currentUser?.access_token;
    if (!token) return;
    let cancelled = false;
    setCatalogState('loading');
    fetchModelCatalog(token)
      .then((data) => {
        if (cancelled) return;
        setModelCatalog(data);
        setCatalogState('ready');
      })
      .catch((err) => {
        if (cancelled) return;
        if (err.code === 'SESSION_EXPIRED') {
          handleLogout();
          return;
        }
        console.warn('Model catalog unavailable:', err.message);
        setCatalogState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [currentUser?.access_token, catalogReloadKey]);

  // Bumped by the picker's Retry, so a backend that comes back up recovers
  // without the user reloading the page.
  const retryCatalog = () => setCatalogReloadKey((k) => k + 1);

  const handleModelChange = (key) => {
    setSelectedModel(key);
    localStorage.setItem('accutax_model', key);
  };

  const handleBriefChange = (on) => {
    setBrief(Boolean(on));
    localStorage.setItem('accutax_brief', on ? '1' : '0');
  };

  const handleSubmitQuery = async (queryText, overrides = {}) => {
    const orgId = activeTenant?.organization_id ?? activeTenant?.id;
    if (activeHistoryId) {
      const openEntry = chatHistory.find((e) => e.id === activeHistoryId);
      if (openEntry?.organizationId != null && String(openEntry.organizationId) !== String(orgId)) {
        return;
      }
    }

    setIsLoading(true);
    setResponseData(null);
    setStreamLogs([]);
    setHistoryDirty(true); // real activity — this session now deserves its position bumped

    // 1. Add User Turn & Assistant Loading Placeholder to conversation
    const userTurn = { role: 'user', content: queryText, timestamp: Date.now() };
    const assistantTurn = {
      role: 'assistant',
      isStreaming: true,
      streamingText: '',
      latestStatus: 'Understanding request',
      responseData: null,
      timestamp: Date.now(),
    };
    setConversation((prev) => [...prev, userTurn, assistantTurn]);

    const payload = {
      query: queryText,
      organization_id: activeTenant?.organization_id,
      session_id: widgetSessionId || undefined,
      use_api: true,
      model: overrides.model || selectedModel,
      ui_context: uiContext,
      brief: overrides.brief ?? brief,
      // No effort field — the backend defaults every query to 'exhaustive'
      // (capped automatically per model), so there's nothing to send here.
    };

    const token = currentUser?.access_token || '';

    if (payload.model === 'all') {
      // Dev comparison option: one request to /query/all, no streaming —
      // wait for every model, then render every answer at once.
      try {
        const res = await fetchAllModelsResponse(payload, token);
        setConversation((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
            updated[lastIdx] = {
              ...updated[lastIdx],
              isMultiModel: true,
              responses: res.responses || [],
              isStreaming: false,
            };
          }
          return updated;
        });
      } catch (err) {
        const errorRes = {
          answer: `Error: ${err.message}`,
          error: err.message,
          token_usage: { input_tokens: 0, output_tokens: 0, llm_calls: 0, cost_usd: 0, elapsed_seconds: 0 },
          agent_trace: [],
        };
        setConversation((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
            updated[lastIdx] = {
              ...updated[lastIdx],
              responseData: errorRes,
              streamingText: errorRes.answer,
              isStreaming: false,
            };
          }
          return updated;
        });
      } finally {
        setIsLoading(false);
      }
      return;
    }

    if (isStreaming) {
      // Live Server-Sent Events (SSE) Streaming
      streamQueryResponse(
        payload,
        (chunk) => {
          if (chunk.type === 'data_table' || chunk.table) {
            const tableMarkdown = chunk.table || '';
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  tableData: tableMarkdown,
                  latestStatus: chunk.status || 'Data table loaded',
                };
              }
              return updated;
            });
          } else if (chunk.type === 'notice' || chunk.notice) {
            const noticeObj = chunk.notice;
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  notice: noticeObj,
                };
              }
              return updated;
            });
          } else if (chunk.token || chunk.type === 'token') {
            const tokenStr = chunk.token || '';
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  streamingText: (updated[lastIdx].streamingText || '') + tokenStr,
                  latestStatus: chunk.status || updated[lastIdx].latestStatus,
                };
              }
              return updated;
            });
          } else if (chunk.final_result) {
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  responseData: chunk.final_result,
                  notice: chunk.final_result.notice || updated[lastIdx].notice,
                  tableData: chunk.final_result.table_markdown || updated[lastIdx].tableData,
                  streamingText: chunk.final_result.answer || updated[lastIdx].streamingText,
                  isStreaming: false,
                };
              }
              return updated;
            });
            setResponseData(chunk.final_result);
          } else if (chunk.status) {
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  latestStatus: chunk.status,
                  agentSteps: [...(updated[lastIdx].agentSteps || []), chunk.status].filter(
                    (step, i, arr) => step && step !== arr[i - 1],
                  ),
                };
              }
              return updated;
            });
            setStreamLogs((prev) => [...prev, chunk]);
          } else {
            setStreamLogs((prev) => [...prev, chunk]);
          }
        },
        (errMessage) => {
          const fallbackNotice = {
            kind: 'failed',
            code: 'INTERNAL_ERROR',
            title: 'Request Failed',
            message: errMessage,
            suggestions: ['Try rephrasing your question', 'Try again in a moment'],
            retryable: true,
          };
          const errorRes = {
            answer: errMessage,
            error: errMessage,
            status: 'failed',
            notice: fallbackNotice,
            results: [],
            token_usage: { input_tokens: 0, output_tokens: 0, llm_calls: 0, cost_usd: 0, elapsed_seconds: 0 },
            agent_trace: [],
          };
          setConversation((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
              updated[lastIdx] = {
                ...updated[lastIdx],
                responseData: errorRes,
                notice: fallbackNotice,
                streamingText: errorRes.answer,
                isStreaming: false,
              };
            }
            return updated;
          });
          setResponseData(errorRes);
          setIsLoading(false);
        },
        () => {
          setConversation((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
              updated[lastIdx] = {
                ...updated[lastIdx],
                isStreaming: false,
              };
            }
            return updated;
          });
          setIsLoading(false);
        },
        token
      );
    }
 else {
      // Synchronous API Call
      try {
        const res = await fetchQueryResponse(payload, token);
        setConversation((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
            updated[lastIdx] = {
              ...updated[lastIdx],
              responseData: res,
              streamingText: res.answer,
              isStreaming: false,
            };
          }
          return updated;
        });
        setResponseData(res);
      } catch (err) {
        const errorRes = {
          answer: `Error: ${err.message}`,
          error: err.message,
          token_usage: { input_tokens: 0, output_tokens: 0, llm_calls: 0, cost_usd: 0, elapsed_seconds: 0 },
          agent_trace: [],
        };
        setConversation((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
            updated[lastIdx] = {
              ...updated[lastIdx],
              responseData: errorRes,
              streamingText: errorRes.answer,
              isStreaming: false,
            };
          }
          return updated;
        });
        setResponseData(errorRes);
      } finally {
        setIsLoading(false);
      }
    }
  };

  const visibleChatHistory = useMemo(() => {
    const orgId = activeTenant?.organization_id ?? activeTenant?.id;
    if (orgId == null || orgId === '') return [];
    return chatHistory.filter(
      (e) => e.organizationId != null && String(e.organizationId) === String(orgId),
    );
  }, [chatHistory, activeTenant?.organization_id, activeTenant?.id]);

  // If user is not logged in, render the production LoginPage
  if (!currentUser) {
    if (wantsAccutaxHandoff && !handoffTimedOut) {
      return (
        <div className="app-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
          <p style={{ color: 'var(--ink-muted, #64748b)', fontSize: 14 }}>Continuing from Accutax…</p>
        </div>
      );
    }
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  const hasStartedChat = conversation.length > 0;
  const userName = currentUser?.email?.split('@')[0] || 'there';
  const showCanvas = canvasOpen && (canvasSpec || canvasArtifact);



  return (
    <div className="app-layout">
      {/* Left Sidebar */}
      <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <Sidebar
          onNewSession={handleClearConversation}
          chatHistory={visibleChatHistory}
          activeHistoryId={activeHistoryId}
          onSelectHistory={handleSelectHistory}
          onDeleteHistory={handleDeleteHistory}
        />
      </div>

      <div className={`main-content${showCanvas ? ' has-canvas' : ''}`}>
        <div className="chat-column">
        <Header
          sessionTitle={hasStartedChat ? conversation[0].content : ''}
          onNewSession={handleClearConversation}
          onToggleSidebar={() => setSidebarCollapsed(prev => !prev)}
          theme={theme}
          onCycleTheme={cycleTheme}
          activeTenant={activeTenant}
          availableTenants={availableTenants}
          onSelectTenant={handleSelectTenant}
          onOpenHealthModal={() => setShowHealthModal(true)}
        />
        <div className="main-stage">
        <div style={styles.chatScrollArea}>
          <div style={!hasStartedChat ? styles.chatContentIntro : styles.chatContent}>
            {!hasStartedChat ? (
              <>
                <IntroSuggestions
                  onSelectPrompt={handleSubmitQuery}
                  activeTenant={activeTenant}
                  userName={userName}
                />
              </>
            ) : (
              <div style={styles.activeConvoWrapper}>
                <ResponseView
                  conversation={conversation}
                  isLoading={isLoading}
                  streamLogs={streamLogs}
                  activeTenant={activeTenant}
                  onRegenerate={handleSubmitQuery}
                  token={currentUser?.access_token}
                  onOpenCanvas={(block) => {
                    if (block?.spec) setCanvasSpec(block.spec);
                    setCanvasOpen(true);
                  }}
                />
              </div>
            )}
            <div ref={scrollBottomRef} />
          </div>
        </div>
        </div>

      <div style={styles.stickyBottomBar}>
        <div style={styles.inputInnerWrapper}>
          <QueryInput
            onSubmitQuery={handleSubmitQuery}
            isLoading={isLoading}
            isStreaming={isStreaming}
            setIsStreaming={setIsStreaming}
            variant="compact"
            catalog={modelCatalog}
            catalogState={catalogState}
            onRetryCatalog={retryCatalog}
            model={selectedModel}
            onModelChange={handleModelChange}
            brief={brief}
            onBriefChange={handleBriefChange}
          />
        </div>
        <p style={{ textAlign: 'center', fontSize: '0.65rem', color: 'var(--ink-faint)', marginTop: '12px' }}>
          AccuTax AI · AccuTax Pro · Data is processed securely
        </p>
      </div>
        </div>

        {showCanvas && (
          <AnswerCanvas
            spec={canvasSpec}
            artifact={canvasArtifact}
            artifacts={canvasArtifacts}
            token={currentUser?.access_token}
            width={canvasWidth}
            onWidthChange={setCanvasWidth}
            onClose={() => setCanvasOpen(false)}
          />
        )}

      {/* Tenant Authentication & Switcher Modal */}
      {showAuthModal && (
        <TenantLoginModal
          currentTenant={activeTenant}
          onSelectTenant={(newTenant) => setActiveTenant(newTenant)}
          onClose={() => setShowAuthModal(false)}
        />
      )}

      {showHealthModal && (
        <ModelHealthModal
          token={currentUser?.access_token || ''}
          onClose={() => setShowHealthModal(false)}
        />
      )}

      </div>
    </div>
  );
}

const styles = {
  chatScrollArea: {
    flex: '1',
    minHeight: 0,
    overflowY: 'auto',
    padding: '20px 20px',
    scrollBehavior: 'smooth',
    display: 'flex',
    flexDirection: 'column',
  },
  chatContent: {
    maxWidth: 'var(--content-width)',
    width: '100%',
    margin: '0 auto',
  },
  chatContentIntro: {
    maxWidth: 'var(--content-width)',
    width: '100%',
    margin: 'auto',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroCenterContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '20px 0 10px',
    textAlign: 'center',
    maxHeight: 'calc(100vh - 200px)',
  },
  heroHeader: {
    marginBottom: '4px',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
  },
  heroSparkleCircle: {
    width: '40px',
    height: '40px',
    borderRadius: 'var(--radius-md)',
    backgroundColor: 'var(--accent)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: '16px',
  },
  heroGreeting: {
    fontSize: 'var(--text-2xl)',
    fontWeight: 700,
    marginBottom: '8px',
    color: 'var(--ink)',
  },
  heroSubtitle: {
    fontSize: '0.9rem',
    color: 'var(--ink-soft)',
    maxWidth: '500px',
    lineHeight: 1.4,
  },
  activeConvoWrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  convoHeaderBar: {
    display: 'flex',
    justifyContent: 'flex-end',
    marginBottom: '8px',
  },
  clearBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 12px',
    borderRadius: 'var(--radius-sm)',
    backgroundColor: 'rgba(var(--danger-rgb), 0.05)',
    border: '1px solid rgba(var(--danger-rgb), 0.15)',
    color: 'var(--danger)',
    fontSize: '0.75rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'background-color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)',
  },
  stickyBottomBar: {
    flexShrink: 0,
    background: 'linear-gradient(180deg, transparent 0%, var(--bg) 20%)',
    padding: '30px 20px 10px',
    zIndex: 40,
  },
  inputInnerWrapper: {
    maxWidth: '850px',
    width: '100%',
    margin: '0 auto',
  },
};
