import React, { useState, useRef, useEffect } from 'react';
import { Check, ChevronDown } from 'lucide-react';

/**
 * ModelPicker — which Claude model answers (GET /api/v1/models).
 *
 * The same agent answers with every model; the pick only changes which model
 * plans and writes. The server decides what a user may pick and hides the
 * picker when there is one model, so this renders nothing then. Styles are the
 * old picker's .pp-* classes in index.css.
 */
function usePopover(open, setOpen) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
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
  }, [open, setOpen]);
  return ref;
}

// "Balanced. Most accurate; ..." -> "Balanced"
const tierOf = (spec) => (spec.description || '').split('.')[0];

export function ModelPicker({ catalog, model, onModelChange, disabled = false }) {
  const [open, setOpen] = useState(false);
  const ref = usePopover(open, setOpen);
  const models = (catalog && catalog.models) || [];
  if (!catalog || catalog.picker_hidden || models.length < 2) return null;

  const active = models.find((m) => m.key === model) || models[0];
  const pick = (key) => {
    onModelChange(key);
    setOpen(false);
  };

  return (
    <div className="pp-wrap" ref={ref}>
      <button
        type="button"
        className={`pp-trigger ${open ? 'is-open' : ''}`}
        onClick={() => !disabled && setOpen(!open)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={disabled ? 'Model can be changed after this answer' : 'Which model answers'}
      >
        <span>{active.label}</span>
        <ChevronDown size={12} className="pp-caret" />
      </button>

      {open && (
        <div className="pp-menu" role="listbox">
          {models.map((m) => {
            const selected = m.key === active.key;
            const price = m.cost_per_mtok && m.cost_per_mtok.input != null
              ? `$${m.cost_per_mtok.input} / $${m.cost_per_mtok.output} per M tokens`
              : '';
            return (
              <button
                key={m.key}
                type="button"
                role="option"
                aria-selected={selected}
                className={`pp-row ${selected ? 'is-selected' : ''}`}
                onClick={() => pick(m.key)}
                title={[m.description, price].filter(Boolean).join(' · ')}
              >
                <span className="pp-row-label">{m.label}</span>
                <span className="pp-tag">{tierOf(m)}</span>
                {selected ? <Check size={15} className="pp-row-check" /> : <span className="pp-row-key" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
