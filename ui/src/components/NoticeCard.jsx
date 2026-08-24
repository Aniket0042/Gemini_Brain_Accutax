import React, { useState } from 'react';

export const NoticeCard = ({ notice, onRetry, onSuggestionClick }) => {
  const [copied, setCopied] = useState(false);

  if (!notice) return null;

  const { kind = 'degraded', title, message, suggestions = [], retryable = false, request_id } = notice;

  const getKindConfig = () => {
    switch (kind) {
      case 'empty':
        return {
          icon: '📭',
          badge: 'Confirmed Zero Records',
          className: 'notice-empty',
        };
      case 'partial':
        return {
          icon: '📊',
          badge: 'Partial View',
          className: 'notice-partial',
        };
      case 'denied':
        return {
          icon: '🔒',
          badge: 'Access Restricted',
          className: 'notice-denied',
        };
      case 'failed':
        return {
          icon: '⚠️',
          badge: 'Request Interrupted',
          className: 'notice-failed',
        };
      case 'degraded':
      default:
        return {
          icon: '⚡',
          badge: 'Degraded Mode',
          className: 'notice-degraded',
        };
    }
  };

  const config = getKindConfig();

  const handleCopyRequestId = () => {
    if (request_id) {
      navigator.clipboard.writeText(request_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className={`notice-card ${config.className}`}>
      <div className="notice-header">
        <div className="notice-title-row">
          <span className="notice-icon">{config.icon}</span>
          <span className="notice-badge">{config.badge}</span>
          {title && <h4 className="notice-title">{title}</h4>}
        </div>
        {request_id && (
          <div className="notice-ref">
            <span className="ref-label">Ref:</span>
            <code className="ref-code" title="Click to copy correlation ID" onClick={handleCopyRequestId}>
              {request_id.slice(0, 8)}...
            </code>
            <button className="copy-ref-btn" onClick={handleCopyRequestId} title="Copy Request ID">
              {copied ? '✓ Copied' : '📋'}
            </button>
          </div>
        )}
      </div>

      {message && <div className="notice-message">{message}</div>}

      {suggestions && suggestions.length > 0 && (
        <div className="notice-suggestions">
          <span className="suggestions-label">Suggestions:</span>
          <div className="suggestion-pills">
            {suggestions.map((suggestion, idx) => (
              <button
                key={idx}
                className="suggestion-pill"
                onClick={() => onSuggestionClick && onSuggestionClick(suggestion)}
                title="Click to run this query"
              >
                💡 {suggestion}
              </button>
            ))}
          </div>
        </div>
      )}

      {retryable && onRetry && (
        <div className="notice-actions">
          <button className="notice-retry-btn" onClick={onRetry}>
            🔄 Retry Query
          </button>
        </div>
      )}
    </div>
  );
};
