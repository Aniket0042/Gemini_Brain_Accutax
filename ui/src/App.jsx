import React, { useState, useEffect, useRef } from 'react';
import { LoginPage } from './components/LoginPage';
import { Header } from './components/Header';
import { QueryInput } from './components/QueryInput';
import { ResponseView } from './components/ResponseView';
import { TenantLoginModal } from './components/TenantLoginModal';
import { ModelHealthModal } from './components/ModelHealthModal';
import { fetchQueryResponse, streamQueryResponse, fetchTenants } from './services/api';
import { Sparkles, Trash2 } from 'lucide-react';

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
  const [showHealthModal, setShowHealthModal] = useState(false);

  // Execution & Streaming State
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(true);
  const [responseData, setResponseData] = useState(null);
  const [streamLogs, setStreamLogs] = useState([]);

  // Conversation History List state
  const [conversation, setConversation] = useState([]);

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
  };

  // Handle Clear Conversation
  const handleClearConversation = () => {
    setConversation([]);
    setResponseData(null);
    setStreamLogs([]);
    setIsLoading(false);
  };

  // Handle Query Submission
  const handleSubmitQuery = async (queryText) => {
    setIsLoading(true);
    setResponseData(null);
    setStreamLogs([]);

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

  return (
    <div style={styles.appContainer}>
      {/* Top Header Navigation */}
      <Header
        tenant={activeTenant}
        availableTenants={availableTenants}
        currentUser={currentUser}
        onSelectTenant={handleSelectTenant}
        onOpenHealthModal={() => setShowHealthModal(true)}
        onLogout={handleLogout}
      />

      {/* Main Scrollable Chat Area */}
      <div style={styles.chatScrollArea}>
        <div style={styles.chatContent}>
          {!hasStartedChat ? (
            /* Centered Initial Hero View (ChatGPT / Claude / Gemini Style) */
            <div style={styles.heroCenterContainer}>
              <div style={styles.heroHeader}>
                <div style={styles.heroSparkleCircle}>
                  <Sparkles size={28} color="#818cf8" />
                </div>
                <h1 style={styles.heroGreeting}>Where should we begin, {userName}?</h1>
                <p style={styles.heroSubtitle}>
                  Ask any financial query across your organization.
                </p>
              </div>

              {/* Centered Hero Query Input Box */}
              <QueryInput
                onSubmitQuery={handleSubmitQuery}
                isLoading={isLoading}
                isStreaming={isStreaming}
                setIsStreaming={setIsStreaming}
                variant="hero"
              />
            </div>
          ) : (
            /* Active Conversation Thread View */
            <div style={styles.activeConvoWrapper}>
              {/* Elegant Floating Clear Conversation Button */}
              <div style={styles.convoHeaderBar}>
                <button 
                  style={styles.clearBtn} 
                  onClick={handleClearConversation}
                  title="Clear conversation history"
                >
                  <Trash2 size={14} />
                  <span>Clear Chat</span>
                </button>
              </div>

              <ResponseView
                conversation={conversation}
                isLoading={isLoading}
                streamLogs={streamLogs}
                activeTenant={activeTenant}
                onRegenerate={handleSubmitQuery}
              />
            </div>
          )}

          {/* Smooth Auto-Scroll Anchor */}
          <div ref={scrollBottomRef} />
        </div>
      </div>

      {/* Fixed Sticky Bottom Query Input Bar (Appears when chat is active) */}
      {hasStartedChat && (
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
        </div>
      )}

      {/* Tenant Authentication & Switcher Modal */}
      {showAuthModal && (
        <TenantLoginModal
          currentTenant={activeTenant}
          onSelectTenant={(newTenant) => setActiveTenant(newTenant)}
          onClose={() => setShowAuthModal(false)}
        />
      )}

      {/* Model Health Diagnostics Modal */}
      {showHealthModal && (
        <ModelHealthModal onClose={() => setShowHealthModal(false)} />
      )}
    </div>
  );
}

const styles = {
  appContainer: {
    height: '100vh',
    maxHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    backgroundColor: '#0b0f19',
  },
  chatScrollArea: {
    flex: '1',
    overflowY: 'auto',
    padding: '20px 20px 20px',
    scrollBehavior: 'smooth',
  },
  chatContent: {
    maxWidth: '1050px',
    width: '100%',
    margin: '0 auto',
  },
  heroCenterContainer: {
    minHeight: 'calc(100vh - 160px)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '20px 0 60px',
    textAlign: 'center',
  },
  heroHeader: {
    marginBottom: '32px',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
  },
  heroSparkleCircle: {
    width: '56px',
    height: '56px',
    borderRadius: '16px',
    backgroundColor: 'rgba(99, 102, 241, 0.12)',
    border: '1px solid rgba(99, 102, 241, 0.3)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: '16px',
    boxShadow: '0 0 30px rgba(99, 102, 241, 0.2)',
  },
  heroGreeting: {
    fontSize: '2.2rem',
    fontWeight: 700,
    marginBottom: '8px',
    background: 'linear-gradient(135deg, #ffffff 0%, #d1d5db 100%)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
  },
  heroSubtitle: {
    fontSize: '1.05rem',
    color: '#9ca3af',
    maxWidth: '540px',
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
    position: 'sticky',
    bottom: 0,
    background: 'linear-gradient(180deg, transparent 0%, rgba(11, 15, 25, 0.8) 15%, rgba(11, 15, 25, 1) 40%)',
    padding: '24px 20px 18px',
    zIndex: 40,
  },
  inputInnerWrapper: {
    maxWidth: '1050px',
    width: '100%',
    margin: '0 auto',
  },
};
