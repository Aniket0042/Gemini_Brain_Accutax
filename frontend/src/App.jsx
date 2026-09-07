import React, { useState, useEffect, useRef } from 'react';
import { LoginPage } from './components/LoginPage';
import { QueryInput } from './components/QueryInput';
import { ResponseView } from './components/ResponseView';
import { TenantLoginModal } from './components/TenantLoginModal';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { fetchQueryResponse, streamQueryResponse, fetchTenants } from './services/api';
import { Sparkles, Trash2, FileText, AlertOctagon, TrendingUp, DollarSign, Activity, FileCheck } from 'lucide-react';

const DEFAULT_ORGANIZATIONS = [
  {
    id: 27,
    organization_id: 27,
    name: 'Professional & Consulting Services_User1_Org4',
    display_name: 'Professional & Consulting Services',
    tag: 'Financials & GL Leader',
    badge_color: '#10b981',
    description: 'Deepest General Ledger, 12.9k invoices (AED 94M), 98.7% posted, P&L, balance sheets, and top customers.',
  },
  {
    id: 25,
    organization_id: 25,
    name: 'Construction & Real Estate_User1_Org2',
    display_name: 'Construction & Real Estate (VAT & Payments)',
    tag: 'VAT & Supplier Payments',
    badge_color: '#a855f7',
    description: 'Sole holder of VAT/tax data in DB (AED 5.65M VAT) and 1,786 supplier payments.',
  },
  {
    id: 154,
    organization_id: 154,
    name: 'Healthcare & Pharmaceuticals_User12_Org1',
    display_name: 'Healthcare & Pharmaceuticals',
    tag: 'Full Modules & Audit',
    badge_color: '#6366f1',
    description: 'Balanced financial records with 12.7k audit trail rows and 2.9k invoice history records.',
  },
  {
    id: 28,
    organization_id: 28,
    name: 'Construction & Real Estate_User1_Org5',
    display_name: 'Construction & Real Estate (Secondary)',
    tag: 'Clean Secondary Tenant',
    badge_color: '#f59e0b',
    description: '5.9k invoices (AED 44.8M), 4.7k bills, 98.4% GL linkage — ideal for multi-tenant isolation testing.',
  },
];

export default function App() {
  // Current Authenticated User & Token State
  const [currentUser, setCurrentUser] = useState(null);

  // Active Tenant Context State
  const [activeTenant, setActiveTenant] = useState(DEFAULT_ORGANIZATIONS[0]);
  const [availableTenants, setAvailableTenants] = useState(DEFAULT_ORGANIZATIONS);

  // UI Modals State
  const [showAuthModal, setShowAuthModal] = useState(false);

  // Execution & Streaming State
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(true);
  const [responseData, setResponseData] = useState(null);
  const [streamLogs, setStreamLogs] = useState([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Conversation History List state
  const [conversation, setConversation] = useState([]);

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

  // Auto-scroll target ref
  const scrollBottomRef = useRef(null);

  // Check stored auth session on initial load
  useEffect(() => {
    const savedUser = localStorage.getItem('gemini_brain_user');
    if (savedUser) {
      try {
        const parsed = JSON.parse(savedUser);
        setCurrentUser(parsed);
        loadUserTenants(parsed.access_token, parsed);
      } catch (e) {
        localStorage.removeItem('gemini_brain_user');
      }
    }
  }, []);

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
          user_id: user?.user_id || 18,
          db_name: 'accutax_bk_1_4',
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
      const firstOrgId = allowed.length > 0 ? allowed[0] : 27;
      selected = DEFAULT_ORGANIZATIONS.find((o) => o.id === firstOrgId) || DEFAULT_ORGANIZATIONS[0];
    }
    setActiveTenant({
      ...selected,
      organization_id: selected.id || selected.organization_id,
      user_id: user?.user_id || 18,
      db_name: 'accutax_bk_1_4',
    });
  };

  // Handle Switch Tenant from Dropdown
  const handleSelectTenant = (tenantObj) => {
    const orgId = tenantObj.id || tenantObj.organization_id;
    const fullTenant = {
      ...tenantObj,
      organization_id: orgId,
      user_id: currentUser?.user_id || 18,
      db_name: 'accutax_bk_1_4',
    };
    setActiveTenant(fullTenant);
    localStorage.setItem('gemini_brain_active_org', String(orgId));
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
        db_name: 'accutax_bk_1_4',
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
    };

    setChatHistory(prev => {
      const existingIdx = activeHistoryId ? prev.findIndex(e => e.id === activeHistoryId) : -1;
      const withEntry = existingIdx !== -1
        ? prev.map((e, i) => (i === existingIdx ? { ...e, ...entryData } : e))
        : [{ id: Date.now(), ...entryData }, ...prev];
      const updated = withEntry
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 30); // keep max 30
      localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      return updated;
    });
    setHistoryDirty(false);
  };

  // Handle Clear Conversation (saves current session to history first)
  const handleClearConversation = () => {
    persistCurrentConversation();
    setConversation([]);
    setActiveHistoryId(null);
    setHistoryDirty(false);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
  };

  // Handle selecting a saved session from the sidebar history list — saves
  // whatever is currently open first, then swaps in the stored transcript.
  const handleSelectHistory = (id) => {
    if (id === activeHistoryId) return;
    persistCurrentConversation();
    const entry = chatHistory.find(e => e.id === id);
    if (!entry) return;
    setConversation(entry.conversation || []);
    setActiveHistoryId(id);
    setHistoryDirty(false);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
  };

  // Handle deleting a saved session — removes it from state and localStorage.
  // If it's the one currently open, clear the active conversation too so a
  // deleted session doesn't keep sitting on screen (and can't be re-saved by
  // a later "New Session" click).
  const handleDeleteHistory = (id) => {
    setChatHistory(prev => {
      const updated = prev.filter(e => e.id !== id);
      localStorage.setItem('accutax_chat_history', JSON.stringify(updated));
      return updated;
    });
    if (id === activeHistoryId) {
      setConversation([]);
      setActiveHistoryId(null);
      setHistoryDirty(false);
      setResponseData(null);
      setStreamLogs([]);
      setIsLoading(false);
    }
  };

  // Handle Query Submission
  const handleSubmitQuery = async (queryText) => {
    setIsLoading(true);
    setResponseData(null);
    setStreamLogs([]);
    setHistoryDirty(true); // real activity — this session now deserves its position bumped

    // 1. Add User Turn & Assistant Loading Placeholder to conversation
    const userTurn = { role: 'user', content: queryText };
    const assistantTurn = { 
      role: 'assistant', 
      isStreaming: true, 
      streamingText: '',
      latestStatus: 'Understanding request',
      responseData: null 
    };
    setConversation((prev) => [...prev, userTurn, assistantTurn]);

    const payload = {
      query: queryText,
      organization_id: activeTenant?.organization_id,
      user_id: activeTenant?.user_id || currentUser?.user_id || 18,
      db_name: activeTenant?.db_name || 'accutax_bk',
      use_api: true,
    };

    const token = currentUser?.access_token || '';

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

  // If user is not logged in, render the production LoginPage
  if (!currentUser) {
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  const hasStartedChat = conversation.length > 0;
  const userName = currentUser?.email?.split('@')[0] || 'there';

  const SUGGESTIONS = [
    {
      title: 'Generate P&L Report',
      desc: 'Detailed profit & loss report with trend analysis.',
      icon: <FileText size={16} />,
      color: '#0ea5e9'
    },
    {
      title: 'Audit Risk Scan',
      desc: 'Scan financial data and flag anomalies or compliance issues.',
      icon: <AlertOctagon size={16} />,
      color: '#ef4444'
    },
    {
      title: 'Tax Optimization',
      desc: 'Find tax savings opportunities from your expenses.',
      icon: <DollarSign size={16} />,
      color: '#f59e0b'
    },
    {
      title: 'Invoice Aging Report',
      desc: 'Overdue invoices, days outstanding & follow-up actions.',
      icon: <FileCheck size={16} />,
      color: '#8b5cf6'
    },
    {
      title: 'Revenue Forecast',
      desc: 'Forecast revenue for next 3 months based on trends.',
      icon: <TrendingUp size={16} />,
      color: '#3b82f6'
    },
    {
      title: 'Expense Breakdown',
      desc: 'Expenses by category with cost reduction suggestions.',
      icon: <Activity size={16} />,
      color: '#10b981'
    }
  ];

  return (
    <div className="app-layout">
      {/* Left Sidebar */}
      <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <Sidebar
          tenant={activeTenant}
          availableTenants={availableTenants}
          onSelectTenant={handleSelectTenant}
          onNewSession={handleClearConversation}
          chatHistory={chatHistory}
          activeHistoryId={activeHistoryId}
          onSelectHistory={handleSelectHistory}
          onDeleteHistory={handleDeleteHistory}
        />
      </div>

      {/* Main Right Content */}
      <div className="main-content" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <Header 
          sessionTitle={hasStartedChat ? conversation[0].content : ''}
          onNewSession={handleClearConversation}
          onToggleSidebar={() => setSidebarCollapsed(prev => !prev)} 
        />
        <div style={styles.chatScrollArea}>
          <div style={styles.chatContent}>
            {!hasStartedChat ? (
              <div style={styles.heroCenterContainer}>
                <div style={styles.heroHeader}>
                  <div style={styles.heroSparkleCircle}>
                    <Sparkles size={20} color="#ffffff" />
                  </div>
                  <h1 style={styles.heroGreeting}>How can I help you today?</h1>
                  <p style={styles.heroSubtitle}>
                    I'm your AI financial assistant. I generate reports, analyze finances, optimize taxes, and automate accounting workflows.
                  </p>
                </div>

                <div className="suggestion-grid">
                  {SUGGESTIONS.map((s, idx) => (
                    <div key={idx} className="suggestion-card" onClick={() => handleSubmitQuery(s.title)}>
                      <div className="suggestion-icon" style={{ color: s.color }}>
                        {s.icon}
                      </div>
                      <div className="suggestion-content">
                        <h4>{s.title}</h4>
                        <p>{s.desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div style={styles.activeConvoWrapper}>
                <ResponseView
                  conversation={conversation}
                  isLoading={isLoading}
                  streamLogs={streamLogs}
                  activeTenant={activeTenant}
                  onRegenerate={handleSubmitQuery}
                />
              </div>
            )}
            <div ref={scrollBottomRef} />
          </div>
        </div>

      {/* Bottom Query Input Bar */}
      <div style={styles.stickyBottomBar}>
        <div style={styles.inputInnerWrapper}>
          <QueryInput
            onSubmitQuery={handleSubmitQuery}
            isLoading={isLoading}
            isStreaming={isStreaming}
            setIsStreaming={setIsStreaming}
            variant="compact"
          />
        </div>
        <p style={{ textAlign: 'center', fontSize: '0.65rem', color: '#94a3b8', marginTop: '12px' }}>
          AccuTax AI · AccuTax Pro · Data is processed securely
        </p>
      </div>

      {/* Tenant Authentication & Switcher Modal */}
      {showAuthModal && (
        <TenantLoginModal
          currentTenant={activeTenant}
          onSelectTenant={(newTenant) => setActiveTenant(newTenant)}
          onClose={() => setShowAuthModal(false)}
        />
      )}

      </div>
    </div>
  );
}

const styles = {
  chatScrollArea: {
    flex: '1',
    overflowY: 'auto',
    padding: '24px 20px 20px',
    scrollBehavior: 'smooth',
  },
  chatContent: {
    maxWidth: '850px',
    width: '100%',
    margin: '0 auto',
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
    borderRadius: '10px',
    backgroundColor: '#0e8a75',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: '16px',
  },
  heroGreeting: {
    fontSize: '1.5rem',
    fontWeight: 700,
    marginBottom: '8px',
    color: '#1e293b',
  },
  heroSubtitle: {
    fontSize: '0.9rem',
    color: '#64748b',
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
    borderRadius: '8px',
    backgroundColor: 'rgba(244, 63, 94, 0.05)',
    border: '1px solid rgba(244, 63, 94, 0.15)',
    color: '#f43f5e',
    fontSize: '0.75rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  stickyBottomBar: {
    flexShrink: 0,
    background: 'linear-gradient(180deg, rgba(248, 250, 252, 0) 0%, rgba(248, 250, 252, 1) 20%)',
    padding: '30px 20px 10px',
    zIndex: 40,
  },
  inputInnerWrapper: {
    maxWidth: '850px',
    width: '100%',
    margin: '0 auto',
  },
};
