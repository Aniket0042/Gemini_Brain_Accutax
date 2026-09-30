import React from 'react';
import { ExternalLink } from 'lucide-react';

/**
 * ActionButtonBlock — {label, url}. Emitted only for App Guidance / FAQ
 * answers whose guide section maps to a real Accutax route (see backend
 * knowledge/guide_loader.py SECTION_ROUTES). Renders as a real button
 * instead of a markdown hyperlink so it can't be mistaken for prose the
 * model wrote, and can't carry a hallucinated URL — the backend builds
 * `url` deterministically, this component only ever opens what it's given.
 *
 * Reuses the .report-action pill styling already used for
 * Preview/Download/Export so this reads as the same family of action, not
 * a one-off. Opens in a new tab: this chat panel and the Accutax screen it
 * links to are the same app, but a new tab keeps the chat thread intact
 * instead of navigating the user away from it.
 */
export function ActionButtonBlock({ block }) {
  if (!block?.url) return null;

  return (
    <div className="report-actions">
      <button
        type="button"
        className="report-action"
        onClick={() => window.open(block.url, '_blank', 'noopener,noreferrer')}
      >
        <ExternalLink size={14} />
        {block.label || 'Open in Accutax'}
      </button>
    </div>
  );
}
