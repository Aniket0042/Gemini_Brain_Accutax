import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Building2, ChevronDown, Check, Search, X } from 'lucide-react';

const idOf = (t) => t?.id ?? t?.organization_id;
const nameOf = (t) => t?.display_name || t?.name || t?.org_name || `Organization ${idOf(t)}`;

/**
 * TenantSwitcher — the org-context dropdown, lives in the header. Reuses the
 * same pp-* popover classes as the answer canvas menus so every
 * dropdown in the app shares one visual language.
 *
 * With `multiOrgEnabled`, each row is a checkbox and the choice is committed
 * with Apply rather than on every click: a new selection starts a new chat,
 * so ticking three boxes must not reset the conversation three times.
 * `maxOrgs` comes from the backend; boxes beyond it are disabled.
 *
 * The menu is portaled to <body> and positioned with `fixed` coordinates
 * from the trigger's own rect, rather than absolutely inside the normal
 * flow: the header sits inside .main-content, which is `overflow: hidden`
 * (it has to be, to keep the chat's own scroll regions from spilling into a
 * page-level scrollbar) — an absolutely-positioned menu anchored there gets
 * silently clipped the instant it grows past the header's bottom edge.
 */
export function TenantSwitcher({
  tenant,
  availableTenants = [],
  onSelectTenant,
  selectedOrgIds = [],
  onSelectOrgs,
  multiOrgEnabled = false,
  maxOrgs = 1,
}) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState(null);
  const [pending, setPending] = useState([]);
  const [search, setSearch] = useState('');
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

  const multi = multiOrgEnabled && maxOrgs > 1 && typeof onSelectOrgs === 'function';
  const activeId = idOf(tenant);
  const committed = selectedOrgIds.length ? selectedOrgIds.map(String) : (activeId != null ? [String(activeId)] : []);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (
        triggerRef.current && !triggerRef.current.contains(e.target) &&
        menuRef.current && !menuRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const toggleOpen = () => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setMenuPos({ top: rect.bottom + 8, left: rect.left });
      // Closing without Apply discards unticked changes.
      setPending(committed);
      setSearch('');
    }
    setOpen((v) => !v);
  };

  if (!tenant && availableTenants.length === 0) return null;

  // Two orgs can share a name (e.g. two "…_Org4"). Those get an ID chip in
  // the menu and the ID in the button label, so they are never confused.
  const nameCounts = availableTenants.reduce((acc, t) => {
    acc[nameOf(t)] = (acc[nameOf(t)] || 0) + 1;
    return acc;
  }, {});
  const isDuplicateName = (t) => (nameCounts[nameOf(t)] || 0) > 1;
  const labelOf = (t) => (isDuplicateName(t) ? `${nameOf(t)} (ID ${idOf(t)})` : nameOf(t));
  const idChip = (t) => (isDuplicateName(t)
    ? <span className="pp-tag" title="Another organization has the same name">ID {idOf(t)}</span>
    : null);

  const selectedTenants = availableTenants.filter((t) => committed.includes(String(idOf(t))));
  let label = tenant ? labelOf(tenant) : 'Select organization';
  if (multi && selectedTenants.length > 1) {
    label = `${labelOf(selectedTenants[0])} +${selectedTenants.length - 1}`;
  }

  const pick = (t) => {
    onSelectTenant && onSelectTenant(t);
    setOpen(false);
  };

  const togglePending = (id) => {
    const key = String(id);
    setPending((prev) => {
      if (prev.includes(key)) return prev.filter((p) => p !== key);
      if (prev.length >= maxOrgs) return prev;
      return [...prev, key];
    });
  };

  // Every word typed must appear in the org's name, ID, tag or industry.
  const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (t) => {
    if (!terms.length) return true;
    const haystack = [nameOf(t), t.name, t.tag, t.industry, idOf(t)].filter(Boolean).join(' ').toLowerCase();
    return terms.every((term) => haystack.includes(term));
  };
  const visibleTenants = terms.length ? availableTenants.filter(matches) : availableTenants;
  const visibleIds = visibleTenants.map((t) => String(idOf(t)));
  const allVisibleChecked = visibleIds.length > 0 && visibleIds.every((id) => pending.includes(id));

  // Select all acts on the orgs in view: every org when nothing is typed,
  // the matches otherwise. Ticks outside the matches are kept.
  const toggleAllVisible = () => {
    setPending((prev) => {
      if (allVisibleChecked) return prev.filter((p) => !visibleIds.includes(p));
      const next = [...prev];
      for (const id of visibleIds) {
        if (next.length >= maxOrgs) break;
        if (!next.includes(id)) next.push(id);
      }
      return next;
    });
  };

  // Enter picks the only match, so an org can be chosen from the keyboard.
  const onSearchKey = (e) => {
    if (e.key !== 'Enter' || visibleTenants.length !== 1) return;
    e.preventDefault();
    if (multi) togglePending(idOf(visibleTenants[0]));
    else pick(visibleTenants[0]);
  };

  const selectAllLabel = terms.length
    ? `Select all matches (${visibleIds.length})`
    : availableTenants.length > maxOrgs ? `Select first ${maxOrgs}` : 'Select all';
  const pendingChanged =
    pending.length !== committed.length || pending.some((p) => !committed.includes(p));

  const apply = () => {
    if (pending.length === 0) return;
    // Keep the dropdown's own order so the primary org is stable.
    const ordered = availableTenants
      .map((t) => idOf(t))
      .filter((id) => pending.includes(String(id)))
      .map(Number);
    onSelectOrgs(ordered);
    setOpen(false);
  };

  const title = multi && selectedTenants.length > 1
    ? selectedTenants.map(labelOf).join(', ')
    : 'Switch organization';

  return (
    <div className="pp-wrap" style={{ position: 'static' }}>
      <button
        ref={triggerRef}
        type="button"
        className={`pp-trigger ${open ? 'is-open' : ''}`}
        onClick={toggleOpen}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={title}
        style={styles.trigger}
      >
        <Building2 size={15} style={{ flexShrink: 0 }} />
        <span style={styles.label}>{label}</span>
        <ChevronDown size={13} className="pp-caret" />
      </button>

      {open && menuPos && createPortal(
        <div
          ref={menuRef}
          className="pp-menu pp-menu-down"
          style={{ position: 'fixed', top: menuPos.top, left: menuPos.left, bottom: 'auto' }}
          role="listbox"
          aria-multiselectable={multi || undefined}
        >
          {availableTenants.length > 0 && (
            <div className="pp-search">
              <Search size={14} className="pp-search-icon" aria-hidden="true" />
              <input
                type="text"
                className="pp-search-input"
                placeholder="Search organizations"
                aria-label="Search organizations"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={onSearchKey}
                autoFocus
              />
              {search && (
                <button type="button" className="pp-search-clear" aria-label="Clear search" onClick={() => setSearch('')}>
                  <X size={13} />
                </button>
              )}
            </div>
          )}
          {availableTenants.length === 0 ? (
            <div style={styles.empty}>No organizations available</div>
          ) : visibleTenants.length === 0 ? (
            <div style={styles.empty}>No organizations match “{search.trim()}”</div>
          ) : multi ? (
            <>
              <button
                type="button"
                className={`pp-row ${allVisibleChecked ? 'is-selected' : ''}`}
                onClick={toggleAllVisible}
                title={availableTenants.length > maxOrgs ? `Up to ${maxOrgs} organizations at a time` : undefined}
              >
                <span className={`pp-checkbox ${allVisibleChecked ? 'is-checked' : ''}`} aria-hidden="true">
                  {allVisibleChecked && <Check size={12} strokeWidth={3} />}
                </span>
                <span className="pp-row-label">{selectAllLabel}</span>
              </button>
              <div className="pp-divider" />
              {visibleTenants.map((t) => {
                const id = idOf(t);
                const checked = pending.includes(String(id));
                const atLimit = !checked && pending.length >= maxOrgs;
                return (
                  <button
                    type="button"
                    key={id}
                    className={`pp-row ${checked ? 'is-selected' : ''} ${atLimit ? 'is-off' : ''}`}
                    role="option"
                    aria-selected={checked}
                    disabled={atLimit}
                    title={atLimit ? `Up to ${maxOrgs} organizations at a time` : undefined}
                    onClick={() => togglePending(id)}
                  >
                    <span className={`pp-checkbox ${checked ? 'is-checked' : ''}`} aria-hidden="true">
                      {checked && <Check size={12} strokeWidth={3} />}
                    </span>
                    <span className="pp-row-label" title={labelOf(t)}>{nameOf(t)}</span>
                    {idChip(t)}
                    {t.currency && <span className="pp-row-key">{t.currency}</span>}
                  </button>
                );
              })}
              <div className="pp-divider" />
              <div style={styles.foot}>
                <span style={styles.count}>{pending.length} selected</span>
                <button
                  type="button"
                  className="btn btn-primary"
                  style={styles.apply}
                  disabled={pending.length === 0 || !pendingChanged}
                  onClick={apply}
                >
                  Apply{pending.length > 1 ? ` (${pending.length})` : ''}
                </button>
              </div>
              {pendingChanged && (
                <div style={styles.hint}>Changing the selection starts a new chat.</div>
              )}
            </>
          ) : (
            visibleTenants.map((t) => {
              const id = idOf(t);
              const selected = String(id) === String(activeId);
              return (
                <button
                  type="button"
                  key={id}
                  className={`pp-row ${selected ? 'is-selected' : ''}`}
                  role="option"
                  aria-selected={selected}
                  onClick={() => pick(t)}
                >
                  <span className="pp-row-label" title={labelOf(t)}>{nameOf(t)}</span>
                  {idChip(t)}
                  {selected && <Check size={15} className="pp-row-check" />}
                </button>
              );
            })
          )}
        </div>,
        document.body
      )}
    </div>
  );
}

const styles = {
  // .pp-trigger's own padding gives it a shorter, content-driven height than
  // the header's other controls (the 32px --control-h-sm icon buttons) — fix
  // it to the same tier so every header control lines up on one row rhythm.
  trigger: {
    height: 'var(--control-h-sm)',
    boxSizing: 'border-box',
  },
  label: {
    maxWidth: '180px',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  empty: {
    padding: '10px 8px',
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-faint)',
  },
  hint: {
    padding: '6px 10px',
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-faint)',
    maxWidth: '268px',
  },
  foot: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '8px',
    padding: '4px 4px 2px 10px',
  },
  count: {
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-faint)',
  },
  apply: {
    height: 'var(--control-h-sm)',
    padding: '0 14px',
  },
};
