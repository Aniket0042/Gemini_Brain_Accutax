import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Building2, ChevronDown, Check } from 'lucide-react';

/**
 * TenantSwitcher — the org-context dropdown, lives in the header. Reuses the
 * same pp-* popover classes as the composer's model/effort pickers so every
 * dropdown in the app shares one visual language.
 *
 * The menu is portaled to <body> and positioned with `fixed` coordinates
 * from the trigger's own rect, rather than absolutely inside the normal
 * flow: the header sits inside .main-content, which is `overflow: hidden`
 * (it has to be, to keep the chat's own scroll regions from spilling into a
 * page-level scrollbar) — an absolutely-positioned menu anchored there gets
 * silently clipped the instant it grows past the header's bottom edge.
 */
export function TenantSwitcher({ tenant, availableTenants = [], onSelectTenant }) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

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
    }
    setOpen((v) => !v);
  };

  if (!tenant && availableTenants.length === 0) return null;

  const activeId = tenant?.organization_id ?? tenant?.id;
  const label = tenant?.display_name || tenant?.name || tenant?.org_name || 'Select organization';

  const pick = (t) => {
    onSelectTenant && onSelectTenant(t);
    setOpen(false);
  };

  return (
    <div className="pp-wrap" style={{ position: 'static' }}>
      <button
        ref={triggerRef}
        type="button"
        className={`pp-trigger ${open ? 'is-open' : ''}`}
        onClick={toggleOpen}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Switch organization"
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
        >
          {availableTenants.length === 0 ? (
            <div style={styles.empty}>No organizations available</div>
          ) : (
            availableTenants.map((t) => {
              const id = t.id ?? t.organization_id;
              const selected = String(id) === String(activeId);
              const name = t.display_name || t.name || t.org_name || `Organization ${id}`;
              return (
                <button
                  type="button"
                  key={id}
                  className={`pp-row ${selected ? 'is-selected' : ''}`}
                  role="option"
                  aria-selected={selected}
                  onClick={() => pick(t)}
                >
                  <span className="pp-row-label">{name}</span>
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
};
