import React, { useState, useEffect, useRef } from 'react';
import { LoginPage } from './components/LoginPage';
import { Header } from './components/Header';
import { QueryInput } from './components/QueryInput';
import { ResponseView } from './components/ResponseView';
import { TenantLoginModal } from './components/TenantLoginModal';
import { ModelHealthModal } from './components/ModelHealthModal';
import { fetchQueryResponse, streamQueryResponse } from './services/api';
import { Sparkles, Trash2 } from 'lucide-react';

const ORG_NAME_MAP = {
  69: 'Accutax LLC (Primary Tenant)',
  27: 'Manufacturing Corp (Secondary Tenant)',
  18: 'TechSolutions Inc (Enterprise Tenant)',
  14: 'Organization #14',
  44: 'Global Logistics (Multi Tenant)',
};

export default function App() {
  // Current Authenticated User & Token State
  const [currentUser, setCurrentUser] = useState(null);

  // Active Tenant Context State
  const [activeTenant, setActiveTenant] = useState(null);

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
        initTenantFromUser(parsed);
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

  // Helper to initialize active tenant from logged in user claims
  const initTenantFromUser = (user) => {
    const allowed = user.allowed_org_ids || [];
    const firstOrgId = allowed.length > 0 ? allowed[0] : 69;
    setActiveTenant({
      organization_id: firstOrgId,
      org_name: ORG_NAME_MAP[firstOrgId] || `Tenant Organization #${firstOrgId}`,
      user_id: user.user_id,
      db_name: 'accutax_bk_1_5',
    });
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
    initTenantFromUser(userSession);
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
    const assistantTurn = { role: 'assistant', isStreaming: true, responseData: null };
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
          if (chunk.final_result) {
            setConversation((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  responseData: chunk.final_result,
                };
              }
              return updated;
            });
            setResponseData(chunk.final_result);
          } else {
            setStreamLogs((prev) => [...prev, chunk]);
          }
        },
        (errMessage) => {
          const errorRes = {
            answer: `Error: ${errMessage}`,
            error: errMessage,
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
    } else {
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
        currentUser={currentUser}
        onOpenAuthModal={() => setShowAuthModal(true)}
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
