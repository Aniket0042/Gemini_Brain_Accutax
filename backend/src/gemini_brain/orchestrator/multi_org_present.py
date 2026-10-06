"""
multi_org_present.py — Compact presentation of multi-organization list answers.

Questions that return rows ("list vendors with overdue bills", "top customers
in each organization", "vendors used by both") used to show every row of every
organization, one full table per org, plus a model-written table on top. Here
the shape of the answer is chosen from the question, the rows are reduced in
code, and the model only summarises the reduced result:

- per_org  : one summary table (count and total per org), then every org's top
             rows in a collapsible section, closed by default;
- merged   : one table across all organizations, top rows only, with an
             Organization column;
- overlap  : only the items found in more than one organization.

The full rows stay available: each org's collapsible section keeps its top
rows, and the result `results` still carries every tagged row.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

PER_ORG = "per_org"
MERGED = "merged"
OVERLAP = "overlap"

#: Rows shown per organization when the question names no number ("top 5"
#: wins), the most shown per organization, and the rows in a merged list.
TOP_PER_ORG = 5
TOP_PER_ORG_MAX = 25
TOP_MERGED = 10
#: Up to this many organizations, every org's section in the collapsed layout starts open.
OPEN_SECTIONS_MAX = 10

_OVERLAP = re.compile(
    # "all organizations" alone is not an overlap ("top customers across all
    # organizations" is a merged list); "common to all…" matches via "common".
    r"\b(both|in\s+common|common|shared|overlap(ping)?)\b"
    # The verb "share" only with contacts: "which vendors do they share", not "share capital".
    r"|\bshare\b(?=.*\b(vendors?|suppliers?|customers?|clients?|payees?)\b)"
    r"|\b(vendors?|suppliers?|customers?|clients?|payees?)\b.*\bshare\b",
    re.IGNORECASE,
)
_MERGED = re.compile(
    r"\b(across|overall|combined|altogether|in\s+total|among\s+all)\b",
    re.IGNORECASE,
)

#: Column names that name the item a row is about, in preference order.
_NAME_KEYS = ("vendor", "customer", "contact_name", "customer_name", "vendor_name",
              "supplier", "supplier_name", "name", "contact", "item_name", "item",
              "account_name", "invoice_number", "bill_number")
#: Words that mark the main amount column.
_AMOUNT_HINTS = ("outstanding", "balance", "total", "amount", "revenue", "spend", "sales", "value", "due")


def is_list_question(question: str) -> bool:
    """Whether the question asks for rows (lists, top-N, breakdowns, overlaps)."""
    from gemini_brain.orchestrator.multi_org_metrics import _LIST_OR_BREAKDOWN

    q = question or ""
    return bool(_LIST_OR_BREAKDOWN.search(q) or _OVERLAP.search(q))


def collapsed_org_sections(runs: List[Any]) -> Dict[str, Any]:
    """Every org's own blocks, each in a section closed by default."""
    sections = []
    for run in runs:
        blocks = [b for b in (run.result or {}).get("blocks") or [] if isinstance(b, dict)] if run.answered else []
        subtitle = "" if run.answered else "could not be retrieved"
        sections.append({"title": run.name, "subtitle": subtitle, "blocks": blocks})
    # The tables are the answer's data: shown open, each org's section open
    # too unless there are many organizations (then one click away).
    return {"type": "collapsible_group", "title": "Details by organization", "default_open": True,
            "sections_open": len(sections) <= OPEN_SECTIONS_MAX, "sections": sections}


def choose_shape(question: str) -> str:
    """How to lay out a list answer across organizations."""
    if _OVERLAP.search(question or ""):
        return OVERLAP
    if _MERGED.search(question or ""):
        return MERGED
    return PER_ORG


def extract_rows(result: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """The item rows in one org's result, or None when no row list is found.

    A payload is either a list of rows, or one dict (a report) holding its
    rows under some key next to a `summary`; the longest list of dicts wins.
    """
    results = (result or {}).get("results") or []
    if results and all(isinstance(r, dict) for r in results):
        if len(results) == 1:
            nested = [v for v in results[0].values()
                      if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
            if nested:
                return max(nested, key=len)
            if isinstance(results[0].get("summary"), dict):
                return []
        return [dict(r) for r in results]
    return None if results else []


def _columns(rows: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """(name column, main amount column) of a row list; either may be None."""
    if not rows:
        return None, None
    keys = list(rows[0].keys())
    name_key = next((k for k in _NAME_KEYS if k in rows[0]), None)
    if name_key is None:
        name_key = next((k for k in keys if isinstance(rows[0].get(k), str)), None)
    numeric = [k for k in keys
               if isinstance(rows[0].get(k), (int, float)) and not isinstance(rows[0].get(k), bool)
               and not k.endswith("id") and k not in ("rank",)]
    amount_key = next((k for hint in _AMOUNT_HINTS for k in numeric if hint in k.lower()), None)
    if amount_key is None and numeric:
        amount_key = numeric[0]
    return name_key, amount_key


def _label(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _table(rows: List[Dict[str, Any]], keys: List[str], caption: str = "", total_rows: Optional[int] = None) -> Dict[str, Any]:
    columns = [{"key": k, "label": _label(k),
                "align": "right" if rows and isinstance(rows[0].get(k), (int, float)) else "left"}
               for k in keys]
    shown = [{k: r.get(k) for k in keys} for r in rows]
    block: Dict[str, Any] = {
        "type": "table",
        "columns": columns,
        "rows": shown,
        "total_rows": total_rows if total_rows is not None else len(shown),
        "truncated": total_rows is not None and total_rows > len(shown),
    }
    if caption:
        block["period"] = caption
    return block


def _display_keys(rows: List[Dict[str, Any]], name_key: Optional[str], amount_key: Optional[str], limit: int = 5) -> List[str]:
    """Up to `limit` columns: name, amount, then the next few simple ones."""
    if not rows:
        return []
    keys = [k for k in (name_key, amount_key) if k]
    for k, v in rows[0].items():
        if len(keys) >= limit:
            break
        if k not in keys and not k.endswith("id") and isinstance(v, (str, int, float)) and not isinstance(v, bool):
            keys.append(k)
    return keys


def _sorted(rows: List[Dict[str, Any]], amount_key: Optional[str]) -> List[Dict[str, Any]]:
    if not amount_key:
        return list(rows)
    return sorted(rows, key=lambda r: r.get(amount_key) if isinstance(r.get(amount_key), (int, float)) else float("-inf"),
                  reverse=True)


def build_presentation(question: str, runs: List[Any]) -> Dict[str, Any]:
    """Blocks and a compact model context for a list answer across organizations.

    `runs` are OrgRun objects. Returns {"shape", "blocks", "prompt"}; the
    prompt holds only reduced figures, never raw rows.
    """
    shape = choose_shape(question)
    per_org: List[Dict[str, Any]] = []
    for run in runs:
        rows = extract_rows(run.result) if run.answered else None
        declared = _declared(run.result) if run.answered else {}
        name_key, amount_key = _columns(rows or [])
        name_key = declared.get("name_key") or name_key
        amount_key = declared.get("amount_key") or amount_key
        # A report that sorted and counted in SQL is trusted: its order is the
        # one asked for, and its count covers every match, not the rows listed.
        per_org.append({"run": run, "rows": rows, "name_key": name_key, "amount_key": amount_key,
                        "sorted": list(rows or []) if declared.get("sorted") else _sorted(rows or [], amount_key),
                        "count": declared.get("match_count"), "total": declared.get("matched_amount"),
                        "order": declared.get("order")})

    if shape == OVERLAP:
        presented = _overlap(per_org, question)
        if presented is not None:
            return presented
        shape = PER_ORG  # no shared name column: fall back to per-org layout
    if shape == MERGED:
        presented = _merged(per_org, question)
        if presented is not None:
            return presented
        shape = PER_ORG
    return _per_org(per_org, question)


def _declared(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The summary a list report states about its own rows (keys, order, full counts)."""
    results = (result or {}).get("results") or []
    if len(results) == 1 and isinstance(results[0], dict) and isinstance(results[0].get("summary"), dict):
        summary = results[0]["summary"]
        return summary if "match_count" in summary else {}
    return {}


def _missing_line(entry: Dict[str, Any]) -> str:
    run = entry["run"]
    return "could not be retrieved" if not run.answered else "no rows"


def _fallback_answer_text(run: Any) -> str:
    """The org's own answer when it came from the full pipeline, not a planned fetch."""
    result = run.result or {}
    if ((result.get("routing_info") or {}).get("path")) == "multi_org_plan":
        return ""
    return (result.get("answer") or "").strip()


def _fmt_amount(value: Any) -> str:
    return f"{value:,.2f}" if isinstance(value, (int, float)) else str(value)


def _per_org(per_org: List[Dict[str, Any]], question: str = "") -> Dict[str, Any]:
    """Summary table, then every org's top rows, open so the data is visible."""
    from gemini_brain.utils.ranking import extract_requested_count

    top_n = extract_requested_count(question, default=TOP_PER_ORG, ceiling=TOP_PER_ORG_MAX)
    summary_rows: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    prompt_lines = [f"Per-organization results (computed; up to {top_n} rows per organization, largest first):"]
    answer_lines: List[str] = []
    # A full-pipeline fallback's text cannot be summarised in code; the model does it then.
    code_answer = True
    counted_by_report = any(e.get("count") is not None for e in per_org)
    amount_label = next((e["amount_key"] for e in per_org if e["amount_key"]), None)
    for entry in per_org:
        run, rows = entry["run"], entry["rows"]
        own_blocks = [b for b in (run.result or {}).get("blocks") or [] if isinstance(b, dict)] if run.answered else []
        fallback_text = _fallback_answer_text(run) if run.answered else ""
        if run.answered and (rows is None or (not rows and fallback_text)):
            # Rows could not be read (or the org fell back to the full pipeline):
            # show the org's own answer and tables rather than "no data".
            blocks = ([{"type": "markdown", "text": fallback_text}] if fallback_text else []) + own_blocks
            sections.append({"title": run.name, "subtitle": "", "blocks": blocks})
            summary_rows.append({"organization": run.name, "items": None})
            prompt_lines.append(f"- {run.name}: " + (fallback_text[:600] if fallback_text else "data shown separately"))
            code_answer = False
            continue
        listed = len(rows or [])
        # The report's own count covers every match; the rows are only the first page.
        count = entry["count"] if entry.get("count") is not None else listed
        total = entry.get("total")
        if total is None and entry["amount_key"] and rows:
            total = round(sum(r.get(entry["amount_key"]) or 0 for r in rows
                              if isinstance(r.get(entry["amount_key"]), (int, float))), 2)
        summary_rows.append({"organization": run.name, "items": count if run.answered else None,
                             "amount": total, "currency": run.currency})
        top = entry["sorted"][:top_n]
        if count and top:
            keys = _display_keys(top, entry["name_key"], entry["amount_key"])
            subtitle = f"{count} item{'s' if count != 1 else ''}" + (f", showing top {len(top)}" if count > len(top) else "")
            sections.append({"title": run.name, "subtitle": subtitle,
                             "blocks": [_table(top, keys, total_rows=count)]})
            amount_part = f", total {total:,.2f} {run.currency}".rstrip() if total is not None else ""
            items = "; ".join(
                f"{r.get(entry['name_key'])}"
                + (f" {_fmt_amount(r.get(entry['amount_key']))}" if entry["amount_key"] else "")
                for r in top if entry["name_key"]
            )
            prompt_lines.append(f"- {run.name}: {count} items{amount_part}" + (f". Top {len(top)}: {items}" if items else ""))
            first_part = ""
            if entry["name_key"] and entry["amount_key"]:
                lead = _LEAD_WORD.get(entry.get("order") or "", "largest")
                first = top[0]
                first_part = f"; {lead}: {first.get(entry['name_key'])}, {_fmt_amount(first.get(entry['amount_key']))}"
            answer_lines.append(f"{run.name}: {count} item{'s' if count != 1 else ''}{amount_part}{first_part}.")
        else:
            reason = _missing_line(entry)
            sections.append({"title": run.name, "subtitle": reason, "blocks": []})
            prompt_lines.append(f"- {run.name}: {reason}")
            answer_lines.append(f"{run.name}: " + ("none found." if run.answered else "could not be retrieved."))

    summary_keys = ["organization", "items"] + (["amount"] if any(r.get("amount") is not None for r in summary_rows) else [])
    if len({r.get("currency") for r in summary_rows if r.get("amount") is not None}) > 1:
        summary_keys.append("currency")
    caption = ("Summary by organization (counts and totals cover every match)" if counted_by_report
               else "Summary by organization (counts and totals cover the rows returned)")
    summary_table = _table(summary_rows, summary_keys, caption=caption)
    if amount_label:
        for col in summary_table["columns"]:
            if col["key"] == "amount":
                col["label"] = _label(amount_label)
    blocks = [
        summary_table,
        # Open: the rows are what the user asked for. Each section can still be closed.
        {"type": "collapsible_group", "title": "Details by organization", "default_open": True,
         "sections_open": True, "sections": sections},
    ]
    counted = [r for r in summary_rows if isinstance(r.get("items"), int)]
    currencies = {r.get("currency") for r in counted if r.get("amount") is not None}
    if counted_by_report and len(counted) > 1 and len(currencies) == 1:
        all_amount = round(sum(r.get("amount") or 0 for r in counted), 2)
        answer_lines.insert(0, (f"All organizations: {sum(r['items'] for r in counted)} items, "
                                f"{all_amount:,.2f} {next(iter(currencies)) or ''}").rstrip() + ".")
    # Written in code only when every org's rows came from a report that counted them.
    answer = "\n".join(f"- {line}" for line in answer_lines) if code_answer and counted_by_report else None
    return {"shape": PER_ORG, "blocks": blocks, "prompt": "\n".join(prompt_lines), "answer": answer}


#: How to name the first row of a sorted list.
_LEAD_WORD = {"amount_desc": "largest", "amount_asc": "smallest", "date_desc": "newest", "date_asc": "oldest"}


def _merged(per_org: List[Dict[str, Any]], question: str = "") -> Optional[Dict[str, Any]]:
    from gemini_brain.utils.ranking import extract_requested_count

    amount_keys = {e["amount_key"] for e in per_org if e["rows"]}
    if len(amount_keys) != 1 or None in amount_keys:
        return None
    (amount_key,) = amount_keys
    name_key = next((e["name_key"] for e in per_org if e["name_key"]), None)
    merged: List[Dict[str, Any]] = []
    currencies = set()
    for entry in per_org:
        run = entry["run"]
        for row in entry["rows"] or []:
            merged.append({**row, "organization": run.name})
            currencies.add(run.currency)
    if len(currencies) > 1:
        return None  # amounts in different currencies cannot share one ranking
    order = next((e.get("order") for e in per_org if e.get("order")), None)
    ranked = _merged_order(merged, amount_key, order)
    top = ranked[:extract_requested_count(question, default=TOP_MERGED, ceiling=TOP_PER_ORG_MAX)]
    keys = ["organization"] + [k for k in _display_keys(top, name_key, amount_key, limit=5) if k != "organization"]
    counted = any(e.get("count") is not None for e in per_org)
    # Every match, not only the rows fetched, when the reports counted them.
    matches = sum(e.get("count") or 0 for e in per_org) if counted else len(merged)
    orgs_with = sum(1 for e in per_org if e["rows"])
    prompt = ["Top items across all organizations (computed):"]
    prompt += [f"{i}. {r.get(name_key)} ({r['organization']}): {r.get(amount_key)}" for i, r in enumerate(top, 1)]
    prompt.append(f"{matches} items in total across {orgs_with} organizations.")
    currency = next(iter(currencies), "") or ""
    lines = [f"- {len(top)} shown of {matches} matching items across {orgs_with} "
             f"organization{'s' if orgs_with != 1 else ''}."]
    lines += [f"{i}. {r.get(name_key)} ({r['organization']}): {_fmt_amount(r.get(amount_key))} {currency}".rstrip()
              for i, r in enumerate(top[:5], 1)]
    none_found = [e["run"].name for e in per_org if e["run"].answered and not e["rows"]]
    failed = [e["run"].name for e in per_org if not e["run"].answered]
    if none_found:
        lines.append("- None found in: " + ", ".join(none_found) + ".")
    if failed:
        lines.append("- Could not be retrieved: " + ", ".join(failed) + ".")
    return {"shape": MERGED, "blocks": [_table(top, keys, caption="Across all organizations", total_rows=matches)],
            "prompt": "\n".join(prompt), "answer": "\n".join(lines) if counted else None}


def _merged_order(rows: List[Dict[str, Any]], amount_key: str, order: Optional[str]) -> List[Dict[str, Any]]:
    """Merged rows in the order the reports used: by amount, or by date for "latest"/"oldest"."""
    if order in ("date_desc", "date_asc") and rows:
        date_key = next((k for k in rows[0] if k.endswith("_date") and k != "due_date"), None)
        if date_key:
            return sorted(rows, key=lambda r: str(r.get(date_key) or ""), reverse=order == "date_desc")
    ranked = _sorted(rows, amount_key)
    return list(reversed(ranked)) if order == "amount_asc" else ranked


def _overlap(per_org: List[Dict[str, Any]], question: str = "") -> Optional[Dict[str, Any]]:
    from gemini_brain.orchestrator.multi_org_lists import overlap_filters

    with_rows = [e for e in per_org if e["rows"] and e["name_key"]]
    if len(with_rows) < 2:
        return None
    filters = overlap_filters(question)
    presence: Dict[str, Dict[str, Any]] = {}
    for entry in with_rows:
        run = entry["run"]
        for row in entry["rows"]:
            name = str(row.get(entry["name_key"]) or "").strip()
            if not name:
                continue
            item = presence.setdefault(name.casefold(), {"item": name, "orgs": {}})
            value = row.get(entry["amount_key"]) if entry["amount_key"] else None
            prev = item["orgs"].get(run.name)
            if isinstance(value, (int, float)):
                item["orgs"][run.name] = (prev or 0) + value
            elif prev is None:
                item["orgs"][run.name] = None
    # "Common to all organizations" means every selected org that answered, not any two.
    answered = [e for e in per_org if e["run"].answered]
    required = max(2, len(answered)) if filters["every_org"] else 2
    shared = [v for v in presence.values() if len(v["orgs"]) >= required]
    for v in shared:
        v["total"] = round(sum(x for x in v["orgs"].values() if isinstance(x, (int, float))), 2)
    if filters["min_total"] is not None:
        shared = [v for v in shared if v["total"] > filters["min_total"]]
    shared.sort(key=lambda v: (-len(v["orgs"]), v["item"].casefold()))
    org_names = [e["run"].name for e in with_rows]
    threshold = filters["min_total"] is not None
    table_rows = []
    for v in shared:
        row: Dict[str, Any] = {"item": v["item"], "organizations": len(v["orgs"])}
        if threshold:
            row["combined_total"] = v["total"]
        for name in org_names:
            row[name] = v["orgs"].get(name)
        table_rows.append(row)
    keys = ["item", "organizations"] + (["combined_total"] if threshold else []) + org_names
    scope = f"all {len(answered)} organizations" if filters["every_org"] else "more than one organization"
    over = f" with a combined total over {filters['min_total']:,.2f}" if threshold else ""
    prompt = [f"Items found in more than one organization (computed, exact name match): {len(shared)}."]
    prompt += [f"- {v['item']}: in {', '.join(v['orgs'])}" for v in shared[:15]]
    if not shared:
        prompt.append("No item appears in more than one organization.")
    currencies = {e["run"].currency for e in with_rows}
    currency = next(iter(currencies)) if len(currencies) == 1 else ""
    lines = [f"{len(shared)} found in {scope}{over} (names matched exactly)."]
    lines += [f"{v['item']}: {len(v['orgs'])} organizations, combined {_fmt_amount(v['total'])} {currency}".rstrip() + "."
              for v in sorted(shared, key=lambda v: -v["total"])[:5]]
    no_rows = [e["run"].name for e in per_org if e not in with_rows]
    if no_rows:
        lines.append("No rows from: " + ", ".join(no_rows) + ".")
    blocks = ([_table(table_rows, keys, caption=f"Found in {scope}{over}", total_rows=len(table_rows))]
              if shared else [])
    return {"shape": OVERLAP, "blocks": blocks, "prompt": "\n".join(prompt),
            "answer": "\n".join(f"- {line}" for line in lines)}
