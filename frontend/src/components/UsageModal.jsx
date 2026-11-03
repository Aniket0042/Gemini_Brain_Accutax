import React, { useEffect, useState } from 'react';
import './usage/usage.css';
import { MyUsage } from './usage/MyUsage';
import { TeamLimits } from './usage/TeamLimits';
import { AdminOverview } from './usage/AdminOverview';
import { AdminOwner } from './usage/AdminOwner';
import { IconClose, IconShield, IconSpark, IconTeam } from './usage/ui';

// AI usage (docs/ORG_TOKEN_LIMITS_STRATEGY.md), one popup with a tab per role:
//   My usage          everyone: their day, the organizations' month, their questions
//   Team & limits     an owner: the month's allowance, the team, daily limits and AI access
//   AI spend          an Accutax admin: spend, what needs attention, owners; an owner's page with plan and credits
// 1 credit = $0.001; a typical question is about 20 credits.

const TABS = [
  { key: 'me', label: 'My usage', show: () => true, icon: IconSpark,
    title: 'Your AI usage', sub: 'Questions you ask the Accutax assistant, across every organization you work in.' },
  { key: 'owner', label: 'Team & limits', show: (u) => u?.is_owner, icon: IconTeam,
    title: 'Team AI and limits', sub: "Your plan's monthly allowance and how your team uses it." },
  { key: 'admin', label: 'AI spend', show: (u) => u?.is_admin, icon: IconShield, badge: 'ACCUTAX ADMIN',
    title: 'AI spend & allowances', sub: 'Every owner, what it costs, and what needs attention.' },
];

export const UsageModal = ({ token, usage, onClose, orgNames = {} }) => {
  const tabs = TABS.filter((t) => t.show(usage));
  const [view, setView] = useState(usage?.is_owner ? 'owner' : usage?.is_admin ? 'admin' : 'me');
  // On the admin tab: the owner opened, and the person to open on their page.
  const [owner, setOwner] = useState(null);
  const tab = tabs.find((t) => t.key === view) || tabs[0];
  const Icon = tab.icon;

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !e.defaultPrevented && e.target.type !== 'search') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content uz-modal" role="dialog" aria-modal="true" aria-labelledby="uz-title" onClick={(e) => e.stopPropagation()}>
        <div className="uz-top">
          <div className="uz-title">
            <span className="uz-title-icon"><Icon /></span>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <h2 id="uz-title">{tab.title}</h2>
                {tab.badge && <span className="uz-badge">{tab.badge}</span>}
              </div>
              <p>{tab.sub}</p>
            </div>
          </div>
          <div className="uz-top-right">
            <button type="button" className="uz-close" onClick={onClose} aria-label="Close"><IconClose /></button>
          </div>
        </div>
        {tabs.length > 1 && (
          <div className="uz-tabs" role="tablist" aria-label="AI usage views">
            {tabs.map((t) => (
              <button key={t.key} type="button" role="tab" className="uz-tab" aria-selected={view === t.key}
                onClick={() => { setView(t.key); setOwner(null); }}>{t.label}</button>
            ))}
          </div>
        )}
        <div className="uz-body">
          {view === 'me' && <MyUsage token={token} orgNames={orgNames} />}
          {view === 'owner' && <TeamLimits token={token} meId={usage?.user_id} />}
          {view === 'admin' && (owner
            ? <AdminOwner key={owner.id} token={token} ownerId={owner.id} focus={owner.person} onBack={() => setOwner(null)} />
            : <AdminOverview token={token} onOpenOwner={(id, person = null) => setOwner({ id, person })} />)}
        </div>
      </div>
    </div>
  );
};
