import React from 'react';
import { ShieldCheck, ShieldAlert, Sparkles, Gauge, Cpu } from 'lucide-react';

/**
 * AnswerProvenance — the strip under an answer saying how it was produced and
 * whether its figures were checked against the retrieved data.
 *
 * For a finance tool this is the trust surface: a number the user can't trace
 * is a number they can't act on. Effort itself isn't user-selectable (every
 * query runs at the highest tier its model supports), so this only reports
 * what was delivered — there's no escalation action, nothing to re-run at
 * "more effort" since it's already at the model's max.
 */
export function AnswerProvenance({ policy, verification }) {
  if (!policy && !verification) return null;

  const delivered = policy?.effort_delivered;
  const degraded = policy?.degraded_reason;

  const checked = verification?.checked ?? 0;
  const grounded = verification?.grounded !== false;
  const unmatched = verification?.unmatched || [];

  return (
    <div className="provenance">
      <div className="provenance-row">
        {policy && (
          <>
            <span className="prov-chip" title="Model that wrote this answer">
              <Cpu size={12} />
              {policy.model_label || policy.model}
            </span>
            <span className="prov-chip" title="Effort level applied">
              <Gauge size={12} />
              {delivered}
            </span>
            {policy.auto && policy.auto_reason && (
              <span className="prov-chip prov-auto" title="Why this was chosen">
                <Sparkles size={12} />
                {policy.auto_reason}
              </span>
            )}
          </>
        )}

        {checked > 0 && (
          <span
            className={`prov-chip ${grounded ? 'prov-ok' : 'prov-warn'}`}
            title={
              grounded
                ? 'Every figure in this answer was found in the retrieved data'
                : 'Some figures could not be traced to the retrieved data'
            }
          >
            {grounded ? <ShieldCheck size={12} /> : <ShieldAlert size={12} />}
            {grounded
              ? `${checked} figure${checked === 1 ? '' : 's'} verified`
              : `${unmatched.length} unverified`}
          </span>
        )}
      </div>

      {degraded && (
        <div className="prov-note">
          Requested {policy.effort_requested}, delivered {delivered} — {degraded}
        </div>
      )}

      {!grounded && unmatched.length > 0 && (
        <div className="prov-note prov-note-warn">
          Could not trace {unmatched.slice(0, 4).join(', ')} to the retrieved rows.
        </div>
      )}
    </div>
  );
}
