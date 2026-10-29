import React, { useState, useEffect, useLayoutEffect, useRef, useMemo } from 'react';
import { LoginPage } from './components/LoginPage';
import { QueryInput } from './components/QueryInput';
import { ContextWindowButton } from './components/ContextWindowMeter';
import { ResponseView } from './components/ResponseView';
import { TenantLoginModal } from './components/TenantLoginModal';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { IntroSuggestions } from './components/IntroSuggestions';
import { ModelHealthModal } from './components/ModelHealthModal';
import { AnswerCanvas } from './components/canvas/AnswerCanvas';
import { fetchQueryResponse, streamQueryResponse, fetchTenants, fetchModelCatalog, fetchSessionMessages, createChatSession, deleteChatSession, fetchUploadConfig, fetchPrivacyAccess, fetchUsage, fetchAgents, redeemHandoffCode } from './services/api';
import { claimCode, handoffCodeFrom, toWidgetHandoff, urlWithoutCode } from './services/handoff';
import { parseMention } from './components/AgentsPanel';
import { newAgentRun, applyAgentStep } from './components/AgentRun';
import { readAgentRuns, recordAgentRun } from './services/agentRuns';
import { dismissedKeys } from './services/findings';
import { UsageModal } from './components/UsageModal';
import { worstOwner, CreditStrip } from './components/UsageMeter';
import { PrivacySnapshotsModal } from './components/PrivacySnapshotsModal';
import { chipsFromBlocks, kindGroup } from './services/attachments';
import { clearUploadBlobs } from './services/uploadBlobs';
import { FilePreviewPanel, ImageLightbox } from './components/FilePreview';
import { Trash2, Bot, X } from 'lucide-react';
import { isSessionId, newSessionId } from './services/sessionId';
import { dropCards } from './sensitive';
import { useAuth0 } from '@auth0/auth0-react';
import BotBuilder from './components/bots/BotBuilder';
import BotList from './components/bots/BotList';
import InboxView from './components/bots/InboxView';
import BotMemoryView from './components/bots/BotMemoryView';
import {
  listBots, updateBot, deleteBot,
  fetchUserBots, deleteBotOnServer, pauseBotOnServer, resumeBotOnServer, migrateLocalBotsToServer,
  fetchBotInbox, markBotInboxSeen, dismissBotFinding, undismissBotFinding,
  approveBotAction, rejectBotAction, editBotAction
} from './services/bots';

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

function mapApiTranscript(messages = [], contextWindow = null) {
  const mapped = messages.map((msg) => {
    if (msg.role === 'user') {
      const attachments = chipsFromBlocks(msg.blocks); // the files this question carried
      return {
        role: 'user',
        content: msg.content,
        timestamp: msg.created_at ? Date.parse(msg.created_at) : Date.now(),
        ...(attachments.length ? { attachments } : {}),
      };
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
  // The meter reads the latest assistant turn's context_window: give a reopened thread its saved total.
  if (contextWindow) {
    const last = mapped.map((m) => m.role).lastIndexOf('assistant');
    if (last >= 0) mapped[last].responseData.context_window = contextWindow;
  }
  return mapped;
}

const DELETED_SESSIONS_KEY = 'accutax_deleted_sessions';
const ACTIVE_SESSION_KEY = 'accutax_active_session';
// One list across every org scope, so a busy org must not evict the history
// of the others.
const HISTORY_LIMIT = 100;
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

// The sorted org ids a saved chat belongs to, as a comparable key. Entries
// saved before multi-org support carry only organizationId.
function entryScopeKey(entry) {
  const ids = entry?.organizationIds?.length
    ? entry.organizationIds
    : (entry?.organizationId != null ? [entry.organizationId] : []);
  return [...new Set(ids.map(Number))].sort((a, b) => a - b).join(',');
}

function entryScopeIds(entry) {
  const key = entryScopeKey(entry);
  return key ? key.split(',').map(Number) : [];
}

const tenantIdOf = (t) => Number(t?.id ?? t?.organization_id);
const tenantNameOf = (t) =>
  t?.display_name || t?.name || t?.org_name || `Organization ${tenantIdOf(t)}`;

function readSavedOrgIds() {
  try {
    const saved = JSON.parse(localStorage.getItem('gemini_brain_active_orgs') || '[]');
    return Array.isArray(saved) ? saved.map(String) : [];
  } catch {
    return [];
  }
}

// Multi-select is offered only when the backend enables it and allows more
// than one org per query; anything else is the single-org dropdown.
function multiOrgFlags(data) {
  const max = Number(data?.max_orgs_per_query) || 1;
  const enabled = Boolean(data?.multi_org_enabled) && max > 1;
  return { enabled, max: enabled ? max : 1 };
}

function stripSessionIdFromUrl() {
  sanitizeSensitiveQueryParams({ includeHandoff: true });
}

export default function App() {
  // Current Authenticated User & Token State
  const [currentUser, setCurrentUser] = useState(() => {
    try {
      const stored = localStorage.getItem('gemini_brain_user');
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });

  // Active Tenant Context State
  const [activeTenant, setActiveTenant] = useState(null);
  const [availableTenants, setAvailableTenants] = useState(DEFAULT_ORGANIZATIONS);
  // Every org the next query covers. activeTenant stays the first of them, so
  // the single-org code paths below keep working unchanged.
  const [selectedOrgIds, setSelectedOrgIds] = useState([]);
  const [multiOrg, setMultiOrg] = useState({ enabled: false, max: 1 });

  // Bots modal & list state (Phase 1 & Phase 2)
  const [isBotsOpen, setIsBotsOpen] = useState(false);
  const [isBuilderOpen, setIsBuilderOpen] = useState(false);
  const [builderStart, setBuilderStart] = useState({ mode: 'blank' });
  const [bots, setBots] = useState([]);
  const [botInbox, setBotInbox] = useState({ unread_count: 0, bots_count: 0, runs: [], findings: [] });
  const [botModalTab, setBotModalTab] = useState('bots'); // 'bots' | 'inbox'
  const [memoryBot, setMemoryBot] = useState(null);
  const [inboxLoading, setInboxLoading] = useState(false);

  const reloadInbox = async () => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      setInboxLoading(true);
      const data = await fetchBotInbox(token);
      setBotInbox(data || { unread_count: 0, bots_count: 0, runs: [], findings: [], approvals: [] });
    } catch (e) {
      console.warn('Failed to fetch bot inbox:', e);
    } finally {
      setInboxLoading(false);
    }
  };

  const reloadBots = async () => {
    const token = currentUser?.access_token;
    if (token) {
      try {
        await migrateLocalBotsToServer(token);
        const serverBots = await fetchUserBots(token);
        setBots(serverBots);
        await reloadInbox();
        return;
      } catch (e) {
        console.warn('Failed to fetch server bots:', e);
      }
    }
    setBots(listBots());
  };

  const handleDismissFinding = async (botId, key, note = '', supplier = '') => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      await dismissBotFinding(botId, key, note, supplier, token);
      await reloadInbox();
    } catch (e) {
      console.warn('Failed to dismiss finding:', e);
    }
  };

  const handleUndismissFinding = async (botId, key) => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      await undismissBotFinding(botId, key, token);
      await reloadInbox();
    } catch (e) {
      console.warn('Failed to restore finding:', e);
    }
  };

  const handleMarkInboxSeen = async () => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      await markBotInboxSeen(token);
      await reloadInbox();
    } catch (e) {
      console.warn('Failed to mark inbox read:', e);
    }
  };

  const handleApproveAction = async (botId, approvalId) => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      await approveBotAction(botId, approvalId, token);
      await reloadInbox();
    } catch (e) {
      console.warn('Failed to approve bot action:', e);
    }
  };

  const handleRejectAction = async (botId, approvalId) => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      await rejectBotAction(botId, approvalId, token);
      await reloadInbox();
    } catch (e) {
      console.warn('Failed to reject bot action:', e);
    }
  };

  const handleEditApproveAction = async (botId, approvalId, updates) => {
    const token = currentUser?.access_token;
    if (!token) return;
    try {
      await editBotAction(botId, approvalId, updates, token);
      await reloadInbox();
    } catch (e) {
      console.warn('Failed to update and approve bot action:', e);
    }
  };

  useEffect(() => {
    reloadBots();
  }, [isBotsOpen, isBuilderOpen, currentUser?.access_token]);

  const handleOpenBots = () => {
    reloadBots();
    setIsBotsOpen(true);
  };

  const handleStartNewBot = (startConfig = { mode: 'blank' }) => {
    setBuilderStart(startConfig);
    setIsBotsOpen(false);
    setIsBuilderOpen(true);
  };

  const handleToggleBot = async (id) => {
    const target = bots.find((b) => b.id === id);
    if (!target) return;
    const token = currentUser?.access_token;
    if (token) {
      try {
        if (target.status === 'active') {
          await pauseBotOnServer(id, token);
        } else {
          await resumeBotOnServer(id, token);
        }
        await reloadBots();
        return;
      } catch (e) {
        console.warn('Server toggle failed, falling back to local:', e);
      }
    }
    const newStatus = target.status === 'active' ? 'paused' : 'active';
    updateBot(id, { status: newStatus });
    setBots(listBots());
  };

  const handleDeleteBot = async (id) => {
    const token = currentUser?.access_token;
    if (token) {
      try {
        await deleteBotOnServer(id, token);
        await reloadBots();
        return;
      } catch (e) {
        console.warn('Server delete failed, falling back to local:', e);
      }
    }
    deleteBot(id);
    setBots(listBots());
  };

  const handleEditBot = (bot) => {
    setBuilderStart({ mode: 'blank', initialDraft: bot.draft, botId: bot.id });
    setIsBotsOpen(false);
    setIsBuilderOpen(true);
  };

  const activeOrgId = activeTenant?.organization_id ?? activeTenant?.id ?? null;
  const scopeOrgIds = useMemo(() => {
    const ids = selectedOrgIds.length
      ? selectedOrgIds
      : (activeOrgId != null && activeOrgId !== '' ? [activeOrgId] : []);
    return [...new Set(ids.map(Number))].sort((a, b) => a - b);
  }, [selectedOrgIds, activeOrgId]);
  const scopeKey = scopeOrgIds.join(',');
  useEffect(() => {
    setAgentRuns(readAgentRuns(scopeKey));
  }, [scopeKey]);

  // activeTenant is also set outside the switcher (login, handoff, the tenant
  // modal). Any org chosen that way replaces a multi-org selection it is not
  // part of, so the two can never disagree.
  useEffect(() => {
    if (activeOrgId == null || activeOrgId === '') {
      setSelectedOrgIds((prev) => (prev.length ? [] : prev));
      return;
    }
    setSelectedOrgIds((prev) =>
      prev.map(String).includes(String(activeOrgId)) ? prev : [Number(activeOrgId)],
    );
  }, [activeOrgId]);

  // UI Modals State
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showHealthModal, setShowHealthModal] = useState(false);
  const [showPrivacy, setShowPrivacy] = useState(false);
  const [privacyAccess, setPrivacyAccess] = useState({ admin: false });
  // AI usage of the chosen organizations' buckets (null when usage tracking is off).
  const [usage, setUsage] = useState(null);
  const [showUsage, setShowUsage] = useState(false);

  // Execution & Streaming State
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(true);
  // AbortController of the query in flight, so the composer's Stop button
  // can cancel it. Null when nothing is running.
  const queryControllerRef = useRef(null);
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

  // The agent answers every chat; the user may pick which Claude model it uses
  // (GET /api/v1/models). The server checks the pick and hides the picker when
  // only one model is offered. Effort is not user-selectable.
  const [brief, setBrief] = useState(() => localStorage.getItem('accutax_brief') === '1');
  const [modelCatalog, setModelCatalog] = useState(null);
  // Specialist agents (GET /agents) and the one that answers the next question.
  const [agents, setAgents] = useState([]);
  const [activeAgent, setActiveAgent] = useState('accutax');
  // Each specialist's last run in this organization scope (this browser only), and the one working now.
  const [agentRuns, setAgentRuns] = useState({});
  const [runningAgent, setRunningAgent] = useState(null);
  // Whether this user may attach files, and the limits (GET /uploads/config). Off until the server says on.
  const [uploadConfig, setUploadConfig] = useState({ enabled: false });
  const [model, setModel] = useState(() => {
    try {
      return localStorage.getItem('accutax_model') || '';
    } catch {
      return '';
    }
  });

  // Conversation History List state
  const [conversation, setConversation] = useState([]);
  const [canvasOpen, setCanvasOpen] = useState(false);
  // A file attached in chat that is open for preview: a document beside the chat, an image over it.
  const [previewFile, setPreviewFile] = useState(null);
  const [lightboxFile, setLightboxFile] = useState(null);
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
      return isSessionId(id) ? id : null;
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
  // Newer dashboard: a one-time code in the URL instead of a postMessage (services/handoff.js).
  const [handoffCode] = useState(() => handoffCodeFrom(window.location.search));
  const [handoffNotice, setHandoffNotice] = useState(null);
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
  const activeHistoryIdRef = useRef(activeHistoryId);
  useEffect(() => {
    activeHistoryIdRef.current = activeHistoryId;
  }, [activeHistoryId]);

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

  // Auto-scroll target ref and container ref
  const scrollBottomRef = useRef(null);
  const chatScrollAreaRef = useRef(null);
  const isSwitchingSessionRef = useRef(false);

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
      // Do not auto-redirect to Auth0: this client isn't registered for
      // every deployment's callback URL, and the app has its own
      // email/password LoginPage that hits the backend directly. Falling
      // through here lets that render instead (see the `!currentUser`
      // branch in the component body).
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
    if (isSessionId(incomingSessionId)) setWidgetSessionId(incomingSessionId);
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
        .slice(0, HISTORY_LIMIT);
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

  // Redeem the dashboard's one-time code once, then continue exactly as a postMessage handoff would.
  useEffect(() => {
    if (!handoffCode) return;
    const clean = urlWithoutCode(window.location.href);
    if (clean) window.history.replaceState({}, '', clean);
    if (!claimCode(handoffCode, sessionStorage)) return;
    redeemHandoffCode(handoffCode)
      .then((res) => {
        const data = toWidgetHandoff(res);
        try {
          sessionStorage.setItem('accutax_widget_handoff', JSON.stringify(data));
        } catch {
          /* ignore quota */
        }
        applyWidgetHandoff(data);
      })
      .catch((err) => {
        setHandoffNotice(err.message || 'Could not continue from the dashboard. Sign in below.');
        sanitizeSensitiveQueryParams({ includeHandoff: true });
        setHandoffTimedOut(true);
        setHandoffSettled(true);
      });
  }, [handoffCode]);

  useEffect(() => {
    if (widgetSessionId) return undefined;
    if (!handoffSettled) return undefined;
    setWidgetSessionId(newSessionId());
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
          scopeOrgIds.length > 1 ? scopeOrgIds : activeTenant.organization_id,
          currentUser.access_token,
          widgetSessionId,
        );
        if (cancelled) return;
        const data = await fetchSessionMessages(widgetSessionId, currentUser.access_token);
        if (cancelled) return;
        const mapped = mapApiTranscript(data.messages || [], data.context_window);
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
  }, [widgetSessionId, currentUser?.access_token, activeTenant?.organization_id, scopeKey]);

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

  // Instant scroll to bottom when switching session (ChatGPT/Claude/Gemini behavior)
  useLayoutEffect(() => {
    if (isSwitchingSessionRef.current) {
      isSwitchingSessionRef.current = false;
      if (chatScrollAreaRef.current) {
        chatScrollAreaRef.current.scrollTop = chatScrollAreaRef.current.scrollHeight;
      } else {
        scrollBottomRef.current?.scrollIntoView({ behavior: 'auto' });
      }
    }
  }, [conversation]);

  // Smooth auto-scroll to bottom only when new response/stream chunk arrives
  useEffect(() => {
    if (isSwitchingSessionRef.current) return;
    if (isLoading || isStreaming || streamLogs.length > 0) {
      scrollBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [responseData, isLoading, streamLogs, isStreaming]);

  // Load accessible tenants from API
  const loadUserTenants = async (token, user) => {
    try {
      const data = await fetchTenants(token);
      if (data && data.tenants && data.tenants.length > 0) {
        setAvailableTenants(data.tenants);
        const flags = multiOrgFlags(data);
        setMultiOrg(flags);
        if (flags.enabled) {
          const saved = readSavedOrgIds();
          const restored = data.tenants
            .map((t) => t.id ?? t.organization_id)
            .filter((id) => saved.includes(String(id)))
            .slice(0, flags.max)
            .map(Number);
          if (restored.length > 1) {
            const primary = data.tenants.find((t) => Number(t.id ?? t.organization_id) === restored[0]);
            setActiveTenant({ ...primary, organization_id: restored[0] });
            setSelectedOrgIds(restored);
            return;
          }
        }
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

  // Switch the org selection. One id is the classic single-org switch; several
  // start a multi-org chat. Either way a new selection means a new chat: the
  // backend fixes each thread to the orgs it started with.
  const handleSelectOrgs = (ids, primaryTenant = null) => {
    const next = [...new Set((ids || []).map(Number))].filter((n) => !Number.isNaN(n));
    if (next.length === 0) return;
    if ([...next].sort((a, b) => a - b).join(',') === scopeKey) return;
    persistCurrentConversation();
    if (!applyScope(next, primaryTenant)) return;
    setConversation([]);
    setActiveHistoryId(null);
    setHistoryDirty(false);
    setWidgetSessionId(null);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
  };

  // Point the org selection at `next` without touching the open conversation.
  // Returns false when the primary org is not one the user can access.
  const applyScope = (next, primaryTenant = null) => {
    const primary =
      primaryTenant ||
      availableTenants.find((t) => tenantIdOf(t) === next[0]);
    if (!primary) return false;
    const previousOrgId = activeOrgId;
    if (previousOrgId != null) {
      setChatHistory((prev) => {
        const updated = prev.map((e) =>
          e.organizationId != null ? e : { ...e, organizationId: previousOrgId },
        );
        localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
        return updated;
      });
    }
    setActiveTenant({ ...primary, organization_id: next[0] });
    setSelectedOrgIds(next);
    localStorage.setItem('gemini_brain_active_org', String(next[0]));
    localStorage.setItem('gemini_brain_active_orgs', JSON.stringify(next));
    return true;
  };

  // A saved chat can reopen only while the user still has every org in it,
  // and multi-org chats only while multi-org querying is on.
  const isScopeAccessible = (ids) => {
    if (ids.length === 0) return false;
    if (ids.length > 1 && (!multiOrg.enabled || ids.length > multiOrg.max)) return false;
    const allowed = new Set(availableTenants.map(tenantIdOf));
    return ids.every((id) => allowed.has(id));
  };

  // Handle Switch Tenant from Dropdown
  const handleSelectTenant = (tenantObj) => {
    handleSelectOrgs([tenantObj.id || tenantObj.organization_id], tenantObj);
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

    // The login response carries no feature flags; /tenants does.
    fetchTenants(userSession.access_token)
      .then((data) => setMultiOrg(multiOrgFlags(data)))
      .catch(() => setMultiOrg({ enabled: false, max: 1 }));

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
    setMultiOrg({ enabled: false, max: 1 });
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
  // Helper to update active history transcript & preview without altering timestamp
  const updateActiveHistoryEntry = (updatedConvo, previewText) => {
    const targetId = activeHistoryIdRef.current;
    if (!targetId) return;
    const firstUser = (updatedConvo || []).find((m) => m.role === 'user');
    setChatHistory((prev) => {
      const idx = prev.findIndex((e) => e.id === targetId);
      if (idx === -1) return prev;
      const existing = prev[idx];
      const updatedEntry = {
        ...existing,
        title: existing.title || (firstUser?.content || '').slice(0, 50),
        preview: previewText ? previewText.slice(0, 60) : existing.preview,
        conversation: updatedConvo,
      };
      const nextList = prev.map((e, i) => (i === idx ? updatedEntry : e));
      try {
        localStorage.setItem('accutax_chat_history', JSON.stringify(nextList));
      } catch { /* ignore */ }
      return nextList;
    });
  };

  // Persist the current conversation into chatHistory — either updating the
  // entry it was loaded from (activeHistoryId) or creating a new one.
  const persistCurrentConversation = () => {
    if (!historyDirty || conversation.length === 0) return;
    const firstUserMsg = conversation.find(m => m.role === 'user');
    if (!firstUserMsg) return;

    const lastAssistant = [...conversation].reverse().find(m => m.role === 'assistant');
    const preview = lastAssistant?.streamingText?.slice(0, 60) || lastAssistant?.responseData?.answer?.slice(0, 60) || 'Processing...';
    
    const targetId = activeHistoryIdRef.current || activeHistoryId || Date.now();
    const existingEntry = chatHistoryRef.current.find((e) => e.id === targetId);

    // Keep existing timestamp if available, else first message timestamp, else now
    const sessionTimestamp =
      existingEntry?.timestamp ||
      (firstUserMsg.timestamp ? new Date(firstUserMsg.timestamp).toISOString() : new Date().toISOString());

    const entryData = {
      id: targetId,
      title: firstUserMsg.content.slice(0, 50),
      preview,
      timestamp: sessionTimestamp,
      conversation, // full transcript, not just a title/preview snippet
      sessionId: widgetSessionId || null,
      organizationId: activeTenant?.organization_id ?? activeTenant?.id ?? null,
      organizationIds: scopeOrgIds,
    };

    setChatHistory(prev => {
      const existingIdx = prev.findIndex((e) => e.id === targetId);
      const withEntry = existingIdx !== -1
        ? prev.map((e, i) => (i === existingIdx ? { ...e, ...entryData } : e))
        : [{ id: targetId, ...entryData }, ...prev];
      const updated = withEntry
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, HISTORY_LIMIT);
      try {
        localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      } catch { /* ignore */ }
      return updated;
    });
    if (!activeHistoryId) setActiveHistoryId(targetId);
    setHistoryDirty(false);
  };

  // Handle Clear Conversation (saves current session to history first)
  const handleClearConversation = () => {
    persistCurrentConversation();
    setConversation([]);
    setActiveHistoryId(null);
    activeHistoryIdRef.current = null;
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
    // A chat from another org selection reopens with the orgs it started
    // with, since the backend fixes each thread to its scope.
    const entryKey = entryScopeKey(entry);
    if (entryKey && entryKey !== scopeKey) {
      const ids = entryScopeIds(entry);
      if (!isScopeAccessible(ids) || !applyScope(ids)) return;
    }
    isSwitchingSessionRef.current = true;
    setConversation(entry.conversation || []);
    setActiveHistoryId(id);
    activeHistoryIdRef.current = id;
    setHistoryDirty(false);
    // Chats saved before ids were always UUIDs ("chat-<ms>") have no server thread; continue them on a new one.
    setWidgetSessionId(isSessionId(entry.sessionId) ? entry.sessionId : newSessionId());
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

  // Handle renaming a session
  const handleRenameHistory = (id, newTitle) => {
    if (!newTitle) return;
    setChatHistory((prev) => {
      const updated = prev.map((e) => (e.id === id ? { ...e, title: newTitle } : e));
      try {
        localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      } catch { /* ignore */ }
      return updated;
    });
  };

  // Handle pinning / unpinning a session
  const handlePinHistory = (id) => {
    setChatHistory((prev) => {
      const updated = prev.map((e) => (e.id === id ? { ...e, isPinned: !e.isPinned } : e));
      try {
        localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      } catch { /* ignore */ }
      return updated;
    });
  };

  // Handle Query Submission
  // Load the models this user may pick. This also checks the stored token: a
  // token the service no longer accepts (most often after a restart) sends the
  // user back to sign in.
  useEffect(() => {
    const token = currentUser?.access_token;
    if (!token) return;
    let cancelled = false;
    fetchModelCatalog(token).then((catalog) => {
      if (cancelled || !catalog) return;
      setModelCatalog(catalog);
      // A remembered pick the server no longer offers falls back to its default.
      const keys = (catalog.models || []).map((m) => m.key);
      setModel((prev) => (keys.includes(prev) ? prev : catalog.default_model || keys[0] || ''));
    }).catch((err) => {
      if (cancelled) return;
      if (err.code === 'SESSION_EXPIRED') {
        handleLogout();
        return;
      }
      console.warn('Session check unavailable:', err.message);
    });
    return () => {
      cancelled = true;
    };
  }, [currentUser?.access_token]);

  useEffect(() => {
    const token = currentUser?.access_token;
    if (!token) return undefined;
    let cancelled = false;
    fetchAgents(token).then((data) => {
      if (cancelled || !data) return;
      setAgents(data.agents || []);
      setActiveAgent((prev) => ((data.agents || []).some((a) => a.key === prev) ? prev : data.default_agent || 'accutax'));
    }).catch((err) => {
      if (!cancelled) console.warn('Agents unavailable:', err.message);
    });
    return () => {
      cancelled = true;
    };
  }, [currentUser?.access_token]);

  useEffect(() => {
    const token = currentUser?.access_token;
    if (!token) {
      setUploadConfig({ enabled: false });
      return undefined;
    }
    let cancelled = false;
    fetchUploadConfig(token).then((config) => {
      if (!cancelled) setUploadConfig(config || { enabled: false });
    });
    return () => {
      cancelled = true;
    };
  }, [currentUser?.access_token]);

  // The privacy page (PII masking snapshots) is shown only to PII_SNAPSHOT_ADMINS.
  useEffect(() => {
    const token = currentUser?.access_token;
    if (!token) {
      setPrivacyAccess({ admin: false });
      return undefined;
    }
    let cancelled = false;
    fetchPrivacyAccess(token).then((access) => {
      if (!cancelled) setPrivacyAccess(access || { admin: false });
    });
    return () => {
      cancelled = true;
    };
  }, [currentUser?.access_token]);

  // AI usage: on sign-in, on a new organization selection, and after each answer (isLoading turns false).
  useEffect(() => {
    const token = currentUser?.access_token;
    if (!token || isLoading) return undefined;
    let cancelled = false;
    fetchUsage(scopeOrgIds, token).then((u) => { if (!cancelled) setUsage(u); });
    return () => { cancelled = true; };
  }, [currentUser?.access_token, scopeKey, isLoading]);

  // A different chat: its files are not this chat's. Close any preview and forget the files fetched for it.
  useEffect(() => {
    setPreviewFile(null);
    setLightboxFile(null);
    clearUploadBlobs();
  }, [widgetSessionId]);

  const handleBriefChange = (on) => {
    setBrief(Boolean(on));
    localStorage.setItem('accutax_brief', on ? '1' : '0');
  };

  const handleModelChange = (key) => {
    setModel(key);
    try {
      localStorage.setItem('accutax_model', key);
    } catch { /* ignore */ }
  };

  const handleSubmitQuery = async (typedText, overrides = {}) => {
    // A card number is removed here, so it is never shown in the chat, kept in this browser or sent.
    const { text: cleanText, cards: cardsRemoved } = dropCards(typedText);
    // "@vat ..." picks the agent for this question only: there is no agent bar to show a sticky choice.
    const mention = parseMention(cleanText, agents);
    if (mention.agentKey && !mention.text.trim()) return;
    const queryText = mention.agentKey ? mention.text : cleanText;
    const agentKey = overrides.agent || mention.agentKey || activeAgent;
    const specialist = agents.find((a) => a.key === agentKey && !a.default);
    if (activeHistoryId) {
      const openEntry = chatHistory.find((e) => e.id === activeHistoryId);
      const openKey = entryScopeKey(openEntry);
      if (openKey && openKey !== scopeKey) {
        return;
      }
    }

    // A query left running (e.g. after switching chats) would otherwise keep
    // writing its chunks into this new turn.
    queryControllerRef.current?.abort();
    queryControllerRef.current = null;

    setIsLoading(true);
    setResponseData(null);
    setStreamLogs([]);
    setHistoryDirty(true); // real activity — this session now deserves its position bumped

    // 1. Add User Turn & Assistant Loading Placeholder to conversation
    const userTurn = {
      role: 'user',
      content: queryText,
      timestamp: Date.now(),
      ...(overrides.attachmentChips?.length ? { attachments: overrides.attachmentChips } : {}),
    };
    const assistantTurn = {
      role: 'assistant',
      isStreaming: true,
      streamingText: '',
      latestStatus: 'Understanding request',
      responseData: null,
      timestamp: Date.now(),
      ...(cardsRemoved ? { sensitiveRemoved: ['CARD'] } : {}),
      ...(specialist ? { agentRun: newAgentRun(specialist, currentScopeNames.join(', ')) } : {}),
    };
    setRunningAgent(specialist ? specialist.key : null);
    const nextConversation = [...conversation, userTurn, assistantTurn];
    setConversation(nextConversation);

    // Immediately create or register the session in sidebar (just like Gemini & ChatGPT)
    let currentHistoryId = activeHistoryIdRef.current;
    let currentSessionId = widgetSessionId;

    if (!currentHistoryId) {
      currentHistoryId = Date.now();
      setActiveHistoryId(currentHistoryId);
      activeHistoryIdRef.current = currentHistoryId;

      if (!currentSessionId) {
        currentSessionId = newSessionId();
        setWidgetSessionId(currentSessionId);
      }

      const newSessionEntry = {
        id: currentHistoryId,
        sessionId: currentSessionId,
        title: queryText.trim().slice(0, 50),
        preview: 'Thinking...',
        timestamp: new Date(userTurn.timestamp).toISOString(),
        conversation: nextConversation,
        organizationId: activeTenant?.organization_id ?? activeTenant?.id ?? null,
        organizationIds: scopeOrgIds,
      };

      setChatHistory((prev) => {
        const withoutOld = prev.filter((e) => e.id !== currentHistoryId);
        const updated = [newSessionEntry, ...withoutOld]
          .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
          .slice(0, HISTORY_LIMIT);
        try {
          localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
        } catch { /* ignore */ }
        return updated;
      });
    } else {
      setChatHistory((prev) => {
        const idx = prev.findIndex((e) => e.id === currentHistoryId);
        if (idx === -1) return prev;
        const existing = prev[idx];
        const updated = prev.map((e, i) =>
          i === idx ? { ...existing, conversation: nextConversation, preview: 'Thinking...' } : e
        );
        try {
          localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
        } catch { /* ignore */ }
        return updated;
      });
    }

    const payload = {
      query: queryText,
      ...(scopeOrgIds.length > 1
        ? { organization_ids: scopeOrgIds }
        : { organization_id: activeTenant?.organization_id }),
      session_id: widgetSessionId || undefined,
      ui_context: uiContext,
      brief: overrides.brief ?? brief,
      ...(overrides.attachments?.length ? { attachments: overrides.attachments } : {}),
      // Without a picker, "auto": the server uses the default model.
      model: (modelCatalog && !modelCatalog.picker_hidden && model) || 'auto',
      ...(agents.length ? { agent: agentKey } : {}),
      // Review findings marked "not a problem" in this browser: the next review leaves them out.
      dismissed_findings: dismissedKeys(),
    };

    const token = currentUser?.access_token || '';

    // Only the request that still owns queryControllerRef may clear loading —
    // a stopped request's late finally must not touch a newer one.
    const releaseQuery = (controller) => {
      if (queryControllerRef.current !== controller) return;
      queryControllerRef.current = null;
      setIsLoading(false);
      setRunningAgent(null);
    };

    if (isStreaming) {
      // Live Server-Sent Events (SSE) Streaming
      const controller = streamQueryResponse(
        payload,
        (chunk) => {
          if (controller.signal.aborted) return;
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
          } else if (chunk.type === 'agent_step') {
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant' && updated[lastIdx].agentRun) {
                updated[lastIdx] = { ...updated[lastIdx], agentRun: applyAgentStep(updated[lastIdx].agentRun, chunk) };
              }
              return updated;
            });
          } else if (chunk.final_result) {
            const ranAgent = chunk.final_result.agent?.key;
            if (ranAgent && chunk.final_result.agent_status && agents.some((a) => a.key === ranAgent && !a.default)) {
              setAgentRuns(recordAgentRun(scopeKey, ranAgent, {
                at: Date.now(), status: chunk.final_result.agent_status, historyId: currentHistoryId, question: queryText,
              }));
            }
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
                  ...(updated[lastIdx].agentRun ? { agentRun: { ...updated[lastIdx].agentRun, finishedAt: Date.now() } } : {}),
                };
              }
              updateActiveHistoryEntry(updated, chunk.final_result.answer);
              return updated;
            });
            setResponseData(chunk.final_result);
          } else if (chunk.status) {
            // Multi-org per-organization progress ("Org4: done") only drives the
            // live status line; listing ten of them under Agent steps is noise.
            const isOrgProgress = chunk.type === 'multi_org' && chunk.organization_id != null;
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                const steps = updated[lastIdx].agentSteps || [];
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  latestStatus: chunk.status,
                  agentSteps: isOrgProgress
                    ? steps
                    : [...steps, chunk.status].filter((step, i, arr) => step && step !== arr[i - 1]),
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
          releaseQuery(controller);
        },
        () => {
          if (controller.signal.aborted) return;
          setConversation((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
              updated[lastIdx] = {
                ...updated[lastIdx],
                isStreaming: false,
              };
            }
            updateActiveHistoryEntry(updated, updated[lastIdx]?.streamingText || updated[lastIdx]?.responseData?.answer);
            return updated;
          });
          releaseQuery(controller);
        },
        token
      );
      queryControllerRef.current = controller;
    }
 else {
      // Synchronous API Call
      const controller = new AbortController();
      queryControllerRef.current = controller;
      try {
        const res = await fetchQueryResponse(payload, token, controller.signal);
        if (controller.signal.aborted) return;
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
        if (controller.signal.aborted) return;
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
        releaseQuery(controller);
      }
    }
  };

  // Stop button: cancel the query in flight and keep whatever text has
  // streamed in so far, marked as stopped.
  const handleStopQuery = () => {
    const controller = queryControllerRef.current;
    if (!controller) return;
    queryControllerRef.current = null;
    controller.abort();
    setConversation((prev) => {
      const updated = [...prev];
      const lastIdx = updated.length - 1;
      if (lastIdx >= 0 && updated[lastIdx].role === 'assistant' && updated[lastIdx].isStreaming) {
        updated[lastIdx] = {
          ...updated[lastIdx],
          isStreaming: false,
          stopped: true,
        };
        updateActiveHistoryEntry(updated, updated[lastIdx].streamingText || 'Response stopped');
      }
      return updated;
    });
    setIsLoading(false);
  };

  // Every saved chat stays listed: those for the current org selection first,
  // the rest under "Other organizations" with a label naming their orgs.
  const historySections = useMemo(() => {
    const inScope = [];
    const otherScope = [];
    if (!scopeKey) return { inScope, otherScope };
    const names = new Map(availableTenants.map((t) => [tenantIdOf(t), tenantNameOf(t)]));
    for (const entry of chatHistory) {
      const key = entryScopeKey(entry);
      if (!key || key === scopeKey) {
        inScope.push(entry);
        continue;
      }
      const ids = entryScopeIds(entry);
      const orgNames = ids.map((id) => names.get(id) || `Organization ${id}`);
      const accessible = isScopeAccessible(ids);
      otherScope.push({
        ...entry,
        scopeLabel: orgNames.length > 1 ? `${orgNames[0]} +${orgNames.length - 1}` : orgNames[0],
        scopeTitle: accessible
          ? orgNames.join(', ')
          : 'You no longer have access to every organization in this chat',
        scopeAccessible: accessible,
      });
    }
    return { inScope, otherScope };
  }, [chatHistory, scopeKey, availableTenants, multiOrg.enabled, multiOrg.max]);

  const currentScopeNames = useMemo(() => {
    const names = new Map(availableTenants.map((t) => [tenantIdOf(t), tenantNameOf(t)]));
    return scopeOrgIds.map((id) => names.get(id) || `Organization ${id}`);
  }, [availableTenants, scopeOrgIds]);

  // If user is not logged in, render the production LoginPage
  if (!currentUser) {
    if (wantsAccutaxHandoff && !handoffTimedOut) {
      return (
        <div className="app-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
          <p style={{ color: 'var(--ink-muted, #64748b)', fontSize: 14 }}>Continuing from Accutax…</p>
        </div>
      );
    }
    return <LoginPage onLoginSuccess={handleLoginSuccess} notice={handoffNotice} />;
  }

  const hasStartedChat = conversation.length > 0;
  const userName = currentUser?.email?.split('@')[0] || 'there';
  const showCanvas = canvasOpen && (canvasSpec || canvasArtifact);
  const showFilePreview = Boolean(previewFile);
  const handlePreviewFile = (item) => {
    if (kindGroup(item.kind) === 'image') setLightboxFile(item);
    else setPreviewFile(item);
  };
  // Most recent assistant turn's context_window snapshot (cumulative session
  // usage against the fixed budget) — scan from the end since not every turn
  // necessarily carries one (e.g. an error envelope built without calling run()).
  // Defaults to a zero-usage snapshot (mirrors backend SESSION_CONTEXT_WINDOW_TOKENS
  // in config/constants.py) so the meter is visible from the very first render,
  // not only after the first response comes back.
  let latestContextWindow = { used: 0, limit: 200_000, percent: 0 };
  for (let i = conversation.length - 1; i >= 0; i--) {
    const cw = conversation[i]?.responseData?.context_window;
    if (cw) { latestContextWindow = cw; break; }
  }



  return (
    <div className="app-layout">
      {/* Left Sidebar */}
      <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <Sidebar
          onNewSession={handleClearConversation}
          chatHistory={historySections.inScope}
          otherScopeHistory={historySections.otherScope}
          scopeNames={currentScopeNames}
          activeHistoryId={activeHistoryId}
          onSelectHistory={handleSelectHistory}
          onDeleteHistory={handleDeleteHistory}
          onRenameHistory={handleRenameHistory}
          onPinHistory={handlePinHistory}
        />
      </div>

      <div className={`main-content${showCanvas || showFilePreview ? ' has-canvas' : ''}`}>
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
          selectedOrgIds={selectedOrgIds}
          onSelectOrgs={handleSelectOrgs}
          multiOrgEnabled={multiOrg.enabled}
          maxOrgs={multiOrg.max}
          onOpenHealthModal={() => setShowHealthModal(true)}
          onOpenPrivacy={privacyAccess.admin ? () => setShowPrivacy(true) : undefined}
          usage={usage}
          onOpenUsage={() => setShowUsage(true)}
          onOpenBots={handleOpenBots}
          botsUnreadCount={botInbox.unread_count || 0}
        />
        <div className="main-stage">
        <div ref={chatScrollAreaRef} style={styles.chatScrollArea}>
          <div style={!hasStartedChat ? styles.chatContentIntro : styles.chatContent}>
            {!hasStartedChat ? (
              <>
                <IntroSuggestions
                  onSelectPrompt={handleSubmitQuery}
                  activeTenant={activeTenant}
                  userName={userName}
                >
                  <div className="bot-home-banner">
                    <Bot size={13} color="var(--accent)" />
                    <span>Let a bot watch your VAT</span>
                    <button
                      type="button"
                      className="bot-home-banner-btn"
                      onClick={() => handleStartNewBot({ mode: 'blank' })}
                    >
                      New bot
                    </button>
                  </div>
                </IntroSuggestions>
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
                  onPreviewFile={handlePreviewFile}
                  onOpenCanvas={(block) => {
                    if (block?.spec) setCanvasSpec(block.spec);
                    setCanvasOpen(true);
                  }}
                  onMakeBot={handleStartNewBot}
                />
              </div>
            )}
            <div ref={scrollBottomRef} />
          </div>
        </div>
        </div>

      <div style={styles.stickyBottomBar}>
        <div style={styles.inputInnerWrapper}>
          <UsageBanner usage={usage} />
          <div style={styles.composerTopMeter}>
            <CreditStrip usage={usage} />
          </div>
          <QueryInput
            onSubmitQuery={handleSubmitQuery}
            onStop={handleStopQuery}
            isLoading={isLoading}
            isStreaming={isStreaming}
            setIsStreaming={setIsStreaming}
            variant="compact"
            brief={brief}
            onBriefChange={handleBriefChange}
            catalog={modelCatalog}
            model={model}
            onModelChange={handleModelChange}
            uploadConfig={uploadConfig}
            sessionId={widgetSessionId}
            orgIds={scopeOrgIds}
            token={currentUser?.access_token || ''}
            onPreviewFile={handlePreviewFile}
          />
          <div style={styles.composerFooter}>
            <div style={styles.composerFooterMeter}>
              <ContextWindowButton contextWindow={latestContextWindow} />
            </div>
          </div>
        </div>
      </div>
        </div>

        {showFilePreview && (
          <FilePreviewPanel
            key={previewFile.id || previewFile.name}
            item={previewFile}
            token={currentUser?.access_token}
            width={canvasWidth}
            onWidthChange={setCanvasWidth}
            onClose={() => setPreviewFile(null)}
          />
        )}
        {!showFilePreview && showCanvas && (
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
        {lightboxFile && (
          <ImageLightbox item={lightboxFile} token={currentUser?.access_token} onClose={() => setLightboxFile(null)} />
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

      {showUsage && usage && (
        <UsageModal token={currentUser?.access_token || ''} usage={usage}
          orgNames={Object.fromEntries(availableTenants.map((t) => [tenantIdOf(t), tenantNameOf(t)]))} onClose={() => { setShowUsage(false); fetchUsage(scopeOrgIds, currentUser?.access_token || '').then(setUsage); }} />
      )}

      {showPrivacy && privacyAccess.admin && (
        <PrivacySnapshotsModal
          token={currentUser?.access_token || ''}
          access={privacyAccess}
          orgIds={scopeOrgIds}
          orgNames={Object.fromEntries(availableTenants.map((t) => [tenantIdOf(t), t?.display_name || t?.name || t?.org_name]).filter(([, n]) => n))}
          onClose={() => setShowPrivacy(false)}
        />
      )}

      {/* Bots Management Modal */}
      {isBotsOpen && (
        <div className="bot-modal-backdrop" onClick={() => { setIsBotsOpen(false); setMemoryBot(null); }}>
          <div className="bot-modal-container" onClick={(e) => e.stopPropagation()}>
            <div className="bot-modal-top">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Bot size={18} color="var(--accent)" />
                  <span style={{ fontWeight: 600, fontSize: 15, color: 'var(--ink)' }}>VAT Bots</span>
                </div>
                <div className="bot-nav-tabs">
                  <button
                    type="button"
                    className={`bot-nav-tab ${botModalTab === 'bots' && !memoryBot ? 'active' : ''}`}
                    onClick={() => { setBotModalTab('bots'); setMemoryBot(null); }}
                  >
                    <span>Bots</span>
                    {bots.length > 0 && <span style={{ opacity: 0.7 }}>({bots.length})</span>}
                  </button>
                  <button
                    type="button"
                    className={`bot-nav-tab ${botModalTab === 'inbox' && !memoryBot ? 'active' : ''}`}
                    onClick={() => { setBotModalTab('inbox'); setMemoryBot(null); }}
                  >
                    <span>Inbox</span>
                    {botInbox.unread_count > 0 && (
                      <span className="bot-unread-badge">{botInbox.unread_count}</span>
                    )}
                  </button>
                </div>
              </div>
              <button
                type="button"
                className="btn-icon-subtle"
                onClick={() => { setIsBotsOpen(false); setMemoryBot(null); }}
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>
            <div className="bot-modal-content">
              {memoryBot ? (
                <BotMemoryView
                  bot={memoryBot}
                  onBack={() => setMemoryBot(null)}
                  onMemoryChanged={() => {
                    reloadInbox();
                    reloadBots();
                  }}
                  token={currentUser?.access_token || ''}
                />
              ) : botModalTab === 'bots' ? (
                <BotList
                  bots={bots}
                  onNewBot={() => handleStartNewBot({ mode: 'blank' })}
                  onEdit={handleEditBot}
                  onToggle={handleToggleBot}
                  onDelete={handleDeleteBot}
                  onViewMemory={(b) => setMemoryBot(b)}
                />
              ) : (
                <InboxView
                  inbox={botInbox}
                  onDismissFinding={handleDismissFinding}
                  onUndismissFinding={handleUndismissFinding}
                  onMarkSeen={handleMarkInboxSeen}
                  onRefresh={reloadInbox}
                  loading={inboxLoading}
                  onApproveAction={handleApproveAction}
                  onRejectAction={handleRejectAction}
                  onEditApproveAction={handleEditApproveAction}
                  onEditBot={(botId) => {
                    const b = bots.find((x) => x.id === botId);
                    if (b) handleEditBot(b);
                  }}
                  onSeeFindingsInChat={(finding) => {
                    setIsBotsOpen(false);
                    handleSubmitQuery(`Investigate VAT issue ${finding.key} (${finding.rule}) on organization ${finding.org_id}`, { agent: 'vat' });
                  }}
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Bot Builder Flow Modal */}
      <BotBuilder
        open={isBuilderOpen}
        onClose={() => {
          setIsBuilderOpen(false);
          setBots(listBots());
        }}
        token={currentUser?.access_token || ''}
        orgIds={scopeOrgIds}
        orgNames={Object.fromEntries(availableTenants.map((t) => [tenantIdOf(t), tenantNameOf(t)]))}
        start={builderStart}
        onSeeFindings={() => {
          setIsBuilderOpen(false);
          handleSubmitQuery('Check our VAT for problems and duplicate bills', { agent: 'vat' });
        }}
      />

      </div>
    </div>
  );
}

// A line above the question box when this month's AI is running low, on the lite model, or used up.
const UsageBanner = ({ usage }) => {
  const owner = worstOwner(usage);
  if (!usage || usage.internal || !owner || usage.enforce === 'off' || usage.enforce === 'log') return null;
  const renews = owner.resets_at ? new Date(owner.resets_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '';
  const mine = owner.user;
  let text = '';
  if (owner.state === 'blocked') text = `This month's AI is used up for ${usage.owners.length > 1 ? 'one of these organizations' : 'this organization'}. It renews on ${renews}.`;
  else if (owner.state === 'lite') text = `This month's AI is used up, so answers come from a faster, lighter model until ${renews}.`;
  else if (mine?.disabled) text = 'The account owner has turned AI off for you for this organization.';
  else if (mine?.percent != null && mine.percent >= 100) text = "You have used today's AI. It is available again at midnight.";
  else if (owner.state === 'warn') text = `${owner.percent}% of this month's AI is used. It renews on ${renews}.`;
  else if (mine?.percent != null && mine.percent >= 80) text = `You have used ${mine.percent}% of your AI for today.`;
  if (!text) return null;
  const strong = owner.state === 'blocked' || mine?.disabled || (mine?.percent ?? 0) >= 100;
  return (
    <div role="status" style={{ fontSize: 12.5, padding: '6px 12px', marginBottom: 6, borderRadius: 8,
      background: strong ? 'var(--danger-soft)' : 'var(--warning-soft)', color: strong ? 'var(--danger-ink)' : 'var(--warning-ink)' }}>
      {text}
    </div>
  );
};

const styles = {
  chatScrollArea: {
    flex: '1',
    minHeight: 0,
    overflowY: 'auto',
    padding: '20px 20px',
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
  composerFooter: {
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '20px',
    marginTop: '6px',
    maxWidth: 'var(--content-width)',
    width: '100%',
    margin: '6px auto 0',
  },
  // Just above the chat box's top right corner: the person's AI for today.
  composerTopMeter: {
    display: 'flex',
    justifyContent: 'flex-end',
    maxWidth: 'var(--content-width)',
    margin: '0 auto 6px',
    padding: '0 6px',
  },
  // Right of the footer line: the context meter.
  composerFooterMeter: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    position: 'absolute',
    right: '6px',
    top: '50%',
    transform: 'translateY(-50%)',
  },
};
