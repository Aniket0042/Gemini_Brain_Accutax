import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Check, ChevronDown, ChevronRight, Info, RefreshCw } from 'lucide-react';

/**
 * PolicyPicker — model selection for the composer.
 *
 * Every query runs at the highest effort tier each model supports — there is
 * no effort picker; the backend defaults to 'exhaustive' and caps it per
 * model automatically. This is the model list only: number shortcuts for the
 * primary models, and a "More models" submenu for the rest.
 */

const AUTO_MODEL = {
  key: 'auto',
  label: 'Auto',
  description: 'Picks the model that fits the question.',
  efforts: ['auto', 'quick', 'standard', 'thorough', 'exhaustive'],
  available: true,
  primary: true,
};

// Dev-only comparison option — not a real model, hits POST /api/v1/query/all
// and runs the query against every available model at once. Kept out of the
// primary row (lands in "More models") so it doesn't compete for space with
// the models people actually pick day to day.
const ALL_MODELS = {
  key: 'all',
  label: 'All Models',
  description: 'Dev: runs the question against every available model and shows every answer.',
  available: true,
  primary: false,
  badge: 'Dev',
};

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

function CatalogNotice({ state, onRetry }) {
  if (state === 'ready') return null;
  if (state === 'loading') return <div className="pp-notice">Loading models…</div>;
  return (
    <div className="pp-notice pp-notice-error">
      <div>Model list unavailable. The service may be starting up or running an older version.</div>
      <button
        type="button"
        className="pp-retry"
        onClick={(e) => {
          e.stopPropagation();
          onRetry();
        }}
      >
        <RefreshCw size={11} /> Retry
      </button>
    </div>
  );
}

function ModelRow({ spec, selected, shortcut, onPick }) {
  const unavailable = spec.available === false;
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      disabled={unavailable}
      className={`pp-row ${selected ? 'is-selected' : ''} ${unavailable ? 'is-off' : ''}`}
      onClick={() => !unavailable && onPick(spec.key)}
      title={spec.description}
    >
      <span className="pp-row-label">{spec.label}</span>
      {spec.badge && (
        <span className="pp-tag" title={spec.description}>
          {spec.badge}
        </span>
      )}
      {unavailable && spec.requires && (
        <span className="pp-tag">
          <Info size={10} /> Requires {spec.requires}
        </span>
      )}
      {selected ? (
        <Check size={15} className="pp-row-check" />
      ) : (
        <span className="pp-row-key">{shortcut || ''}</span>
      )}
    </button>
  );
}

export function ModelMenu({ catalog, catalogState, onRetryCatalog, model, onModelChange }) {
  const [open, setOpen] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const ref = usePopover(open, setOpen);

  const all = useMemo(
    () => [AUTO_MODEL, ...((catalog && catalog.models) || []), ALL_MODELS],
    [catalog],
  );
  const primary = all.filter((m) => m.primary);
  const more = all.filter((m) => !m.primary);
  const active = all.find((m) => m.key === model) || AUTO_MODEL;

  // Number keys pick from the primary list, matching the shortcuts shown.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      const n = parseInt(e.key, 10);
      if (!Number.isNaN(n) && n >= 1 && n <= primary.length) {
        const pick = primary[n - 1];
        if (pick && pick.available !== false) {
          onModelChange(pick.key);
          setOpen(false);
        }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, primary, onModelChange]);

  const pick = (key) => {
    onModelChange(key);
    setOpen(false);
    setShowMore(false);
  };

  return (
    <div className="pp-wrap" ref={ref}>
      <button
        type="button"
        className={`pp-trigger ${open ? 'is-open' : ''}`}
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Which model answers"
      >
        <span>{active.label}</span>
        <ChevronDown size={12} className="pp-caret" />
      </button>

      {open && (
        <div className="pp-menu" role="listbox">
          <CatalogNotice state={catalogState} onRetry={onRetryCatalog} />

          {!showMore ? (
            <>
              {primary.map((m, i) => (
                <ModelRow
                  key={m.key}
                  spec={m}
                  selected={m.key === model}
                  shortcut={i + 1}
                  onPick={pick}
                />
              ))}

              {more.length > 0 && (
                <>
                  <div className="pp-divider" />
                  <button
                    type="button"
                    className="pp-row pp-row-more"
                    onClick={() => setShowMore(true)}
                  >
                    <span className="pp-row-label">More models</span>
                    <ChevronRight size={15} className="pp-row-check" />
                  </button>
                </>
              )}
            </>
          ) : (
            <>
              <button
                type="button"
                className="pp-row pp-row-more"
                onClick={() => setShowMore(false)}
              >
                <ChevronRight size={14} className="pp-back-caret" />
                <span className="pp-row-label">Back</span>
              </button>
              <div className="pp-divider" />
              {more.map((m) => (
                <ModelRow key={m.key} spec={m} selected={m.key === model} onPick={pick} />
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function PolicyPicker({
  catalog,
  catalogState = 'ready',
  onRetryCatalog = () => {},
  model,
  onModelChange,
  disabled = false,
}) {
  if (disabled) return null;
  return (
    <div className="pp-bar">
      <ModelMenu
        catalog={catalog}
        catalogState={catalogState}
        onRetryCatalog={onRetryCatalog}
        model={model}
        onModelChange={onModelChange}
      />
    </div>
  );
}
