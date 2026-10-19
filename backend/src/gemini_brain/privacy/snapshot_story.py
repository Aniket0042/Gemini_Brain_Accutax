"""
snapshot_story.py — One model call of a privacy snapshot as a short story, step by step (PII plan, phase 4).

The full record of a call repeats itself: the conversation as the request held it and again as the model got it,
then the model's answer three times. What a reviewer needs is the difference, once:

  summary   the sentinel's verdict, how many values were hidden and of which kinds, how many lookups the model
            made, and whether every figure went out unchanged (checked, number by number)
  hidden    each hidden value next to the placeholder the model saw, and where it appeared
  question  the user's question as typed and as the model got it
  steps     the model's lookups, grouped by what they asked for: tries, rows, failures, and which fields were
            sent as placeholders
  answer    as the model wrote it and as the user saw it

Built from the snapshot itself, by code: nothing is asked of a model. The full record stays available next to it.
Without the raw side (no key, or past retention) the story shows the sent side only.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gemini_brain.privacy.vault import TOKEN_RX

KIND_LABEL = {"SUPPLIER": "Supplier", "CUSTOMER": "Customer", "PARTY": "Party", "PERSON": "Person",
              "ORG": "Organization", "TRN": "TRN", "EMAIL": "Email", "PHONE": "Phone", "IBAN": "IBAN",
              "ADDRESS": "Address", "EMIRATES_ID": "Emirates ID", "CARD": "Card", "PASSPORT": "Passport",
              "GSTIN": "GSTIN", "PAN": "PAN", "AADHAAR": "Aadhaar", "UPI_ID": "UPI ID"}
TOOL_LABEL = {"query_metrics": "Looked up figures", "list_documents": "Listed documents", "review_vat": "Reviewed VAT",
              "search_vat_kb": "Searched VAT law", "app_guide": "Searched the app guide",
              "cash_forecast": "Forecast cash", "vat_return": "Worked out the VAT return",
              "vat_penalty": "Worked out a VAT penalty", "read_document": "Read an attached file"}
MAX_FIELD_EXAMPLES = 6


# ── Helpers ─────────────────────────────────────────────────────────────────

def _label(m: re.Match) -> str:
    kind, n = (m.group(1), m.group(2)) if m.group(1) is not None else (m.group(3), m.group(4))
    return f"{kind.upper()}_{int(n)}"


def tokens_in(value: Any) -> List[str]:
    """Placeholder labels ("SUPPLIER_3") in a text or JSON value, in order, once each."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return list(OrderedDict.fromkeys(_label(m) for m in TOKEN_RX.finditer(text or "")
                                     if m.group(1) is not None or m.group(3).isupper()))


def _texts(message: Dict[str, Any]) -> List[str]:
    return [b["text"] for b in message.get("content") or [] if isinstance(b, dict) and isinstance(b.get("text"), str)]


def _is_text_turn(message: Dict[str, Any]) -> bool:
    content = [b for b in message.get("content") or [] if isinstance(b, dict)]
    return bool(content) and all("text" in b for b in content)


def question_index(messages: Sequence[Dict[str, Any]]) -> Optional[int]:
    """For snapshots made before the question was recorded: the last user text turn before the model's first
    tool call (or the last user text turn when it called none)."""
    first_tool = next((i for i, m in enumerate(messages) if m.get("role") == "assistant"
                       and any(isinstance(b, dict) and "toolUse" in b for b in m.get("content") or [])), len(messages))
    candidates = [i for i, m in enumerate(messages[:first_tool]) if m.get("role") == "user" and _is_text_turn(m)]
    return candidates[-1] if candidates else None


def _leaves(value: Any, path: str = "", depth: int = 0) -> Iterable[Tuple[str, Any]]:
    """(path, leaf) pairs of a JSON value; the path ends with the field name."""
    if depth > 16:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k), depth + 1)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, f"{path}[{i}]", depth + 1)
    else:
        yield path, value


def _field(path: str) -> str:
    """'rows[3].purchases.vendor_name' -> 'vendor_name'."""
    return re.sub(r"\[\d+\]", "", path).rsplit(".", 1)[-1]


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and bool(re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()))


def _range(labels: Sequence[str]) -> str:
    """['SUPPLIER_1', 'SUPPLIER_2', 'SUPPLIER_3'] -> '[SUPPLIER_1–3]'; mixed or gapped labels are listed."""
    kinds = {l.rsplit("_", 1)[0] for l in labels}
    if len(labels) > 2 and len(kinds) == 1:
        nums = sorted(int(l.rsplit("_", 1)[1]) for l in labels)
        if nums == list(range(nums[0], nums[-1] + 1)):
            return f"[{kinds.pop()}_{nums[0]}–{nums[-1]}]"
    return ", ".join(f"[{l}]" for l in labels)


# ── Tool calls ──────────────────────────────────────────────────────────────

def _pairs(sent: Sequence[Dict[str, Any]], raw: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every tool call of the conversation with its result, on both sides when the raw side is there."""
    results_sent: Dict[str, Any] = {}
    results_raw: Dict[str, Any] = {}
    for side, out in ((sent, results_sent), (raw, results_raw)):
        for m in side or []:
            for b in m.get("content") or []:
                if isinstance(b, dict) and "toolResult" in b:
                    r = b["toolResult"] or {}
                    parts = r.get("content") or []
                    body = next((p.get("json") for p in parts if isinstance(p, dict) and "json" in p),
                                next((p.get("text") for p in parts if isinstance(p, dict) and "text" in p), None))
                    out[r.get("toolUseId")] = body
    calls = []
    for m in sent or []:
        if m.get("role") != "assistant":
            continue
        for b in m.get("content") or []:
            if isinstance(b, dict) and "toolUse" in b:
                use = b["toolUse"] or {}
                uid = use.get("toolUseId")
                calls.append({"name": use.get("name") or "tool", "input": use.get("input") or {},
                              "result": results_sent.get(uid), "raw_result": results_raw.get(uid)})
    return calls


def _signature(call: Dict[str, Any]) -> Tuple:
    """What a lookup asked for, so retries of the same lookup group together (period and limits aside)."""
    i = call["input"] if isinstance(call["input"], dict) else {}
    filters = tuple(sorted(str(f.get("member")) for f in i.get("filters") or [] if isinstance(f, dict)))
    return (call["name"], str(i.get("view") or i.get("type") or ""), filters,
            tuple(sorted(str(g) for g in i.get("group_by") or [])) if not filters else ())


def _describe(call: Dict[str, Any]) -> str:
    i = call["input"] if isinstance(call["input"], dict) else {}
    title = TOOL_LABEL.get(call["name"], call["name"].replace("_", " ").capitalize())
    what = i.get("view") or i.get("type")
    if what:
        title += f": {str(what).replace('_', ' ')}"
    group = [str(g).rsplit(".", 1)[-1].replace("_", " ") for g in i.get("group_by") or []]
    if group:
        title += " by " + ", ".join(group)
    filters = [str(f.get("member")).rsplit(".", 1)[-1].replace("_", " ") for f in i.get("filters") or []
               if isinstance(f, dict)]
    if filters:
        title += ", filtered by " + ", ".join(filters)
    question = i.get("question")
    if isinstance(question, str) and not what:
        title += f": “{question[:80]}”"
    return title


def _rows(result: Any) -> Optional[int]:
    if isinstance(result, dict):
        if isinstance(result.get("row_count"), int):
            return result["row_count"]
        if isinstance(result.get("rows"), list):
            return len(result["rows"])
    return None


def _masked_fields(raw: Any, sent: Any) -> Tuple["OrderedDict[str, List[str]]", int, int]:
    """Placeholder labels per field sent as placeholders, and the figures checked unchanged (checked, changed).
    Without the raw side the figures cannot be compared: (fields, 0, 0)."""
    by_field: "OrderedDict[str, List[str]]" = OrderedDict()
    checked = changed = 0
    raw_leaves = dict(_leaves(raw)) if raw is not None else None
    for path, value in _leaves(sent):
        if raw_leaves is not None:
            before = raw_leaves.get(path)
            # A figure: a number on the raw side that was not hidden (an identifier sent as [TRN_1] is not a figure).
            if _is_number(before) and not (isinstance(value, str) and tokens_in(value)):
                checked += 1
                changed += 0 if str(before) == str(value) else 1
        if isinstance(value, str):
            for label in tokens_in(value):
                labels = by_field.setdefault(_field(path), [])
                if label not in labels:
                    labels.append(label)
    return by_field, checked, changed


def _steps(sent: Sequence[Dict[str, Any]], raw: Sequence[Dict[str, Any]], vault: Dict[str, str]
           ) -> Tuple[List[Dict[str, Any]], int, int, int]:
    groups: "OrderedDict[Tuple, Dict[str, Any]]" = OrderedDict()
    checked = changed = calls = 0
    for call in _pairs(sent, raw):
        calls += 1
        g = groups.setdefault(_signature(call), {"title": _describe(call), "tool": call["name"], "tries": 0,
                                                 "failed": 0, "rows": 0, "rows_known": False, "inputs": [],
                                                 "fields": OrderedDict()})
        g["tries"] += 1
        result = call["result"]
        if isinstance(result, dict) and result.get("error"):
            g["failed"] += 1
        rows = _rows(result)
        if rows is not None:
            g["rows"] += rows
            g["rows_known"] = True
        for label in tokens_in(call["input"]):
            if label not in g["inputs"]:
                g["inputs"].append(label)
        fields, c, ch = _masked_fields(call["raw_result"] if raw else None, result)
        checked, changed = checked + c, changed + ch
        for field, labels in fields.items():
            known = g["fields"].setdefault(field, {"field": field.replace("_", " "), "labels": OrderedDict()})
            for label in labels:
                known["labels"][label] = vault.get(label, "")
    steps = []
    for g in groups.values():
        fields = []
        for f in g["fields"].values():
            labels = list(f["labels"])
            fields.append({"field": f["field"], "placeholders": _range(labels), "count": len(labels),
                           "values": [v for v in f["labels"].values() if v][:MAX_FIELD_EXAMPLES]})
        steps.append({"title": g["title"], "tool": g["tool"], "tries": g["tries"], "failed": g["failed"],
                      "rows": g["rows"] if g["rows_known"] else None,
                      "inputs": [{"placeholder": f"[{l}]", "value": vault.get(l, "")} for l in g["inputs"]],
                      "fields": fields})
    return steps, calls, checked, changed


# ── The story ───────────────────────────────────────────────────────────────

def _where(label: str, *, question: str, system: str, inputs: str, data: str, history: str, answer: str) -> List[str]:
    rx = re.compile(r"\\?\[\s*" + re.escape(label).replace("_", "[ _-]?") + r"\s*\\?\]", re.IGNORECASE)
    places = [("question", question), ("earlier turns", history), ("instructions", system), ("filters", inputs),
              ("data", data), ("answer", answer)]
    return [name for name, text in places if text and rx.search(text)]


def _without_rules(system: str) -> str:
    """The system prompt without the placeholder rules, whose examples ([SUPPLIER_2], [PERSON_1]) hide nothing."""
    from gemini_brain.privacy.masking import PLACEHOLDER_RULES
    return system.replace(PLACEHOLDER_RULES, "")


def _readable(reply: Any) -> str:
    """A report narrator's JSON as text: the headline, then each part as bullets. Any other reply as it is."""
    text = str(reply or "")
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    out = [f"**{data['headline']}**"] if data.get("headline") else []
    for key in ("summary", "drivers", "risks", "actions"):
        items = data.get(key)
        if isinstance(items, list) and items:
            out.append(f"{key.capitalize()}:" + "".join(f"\n- {i}" for i in items))
    return "\n\n".join(out) or text


def build(call: Dict[str, Any], vault: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The story of one call of a snapshot (as snapshots.load or the live demo return it)."""
    vault = vault or {}
    sent = (call.get("sent") or {}).get("messages") or []
    system = _without_rules((call.get("sent") or {}).get("system") or "")
    raw = (call.get("raw") or {}).get("messages") if call.get("raw") else None
    q = call.get("question_index")
    if not isinstance(q, int) or q >= len(sent):
        q = question_index(sent)
    question_sent = "\n".join(_texts(sent[q])) if q is not None else ""
    question_raw = "\n".join(_texts(raw[q])) if (q is not None and raw and q < len(raw)) else None
    history_sent = "\n".join(t for m in sent[:q or 0] for t in _texts(m)) if q else ""
    earlier_turns = sum(1 for m in sent[:q or 0] if m.get("role") == "user")

    steps, lookups, checked, changed = _steps(sent, raw or [], vault)
    inputs_text = json.dumps([s["inputs"] for s in steps], ensure_ascii=False)
    data_text = json.dumps([m for m in sent if any(isinstance(b, dict) and "toolResult" in b
                                                   for b in m.get("content") or [])], ensure_ascii=False)
    reply = str(call.get("reply_model") or "")
    seen = OrderedDict.fromkeys(tokens_in(question_sent) + tokens_in(reply) + tokens_in(data_text)
                                + tokens_in(inputs_text) + tokens_in(history_sent) + tokens_in(system))
    hidden = []
    for label in seen:
        if vault and label not in vault:
            continue  # not a value this request hid (the user typed a placeholder-like text, say)
        kind = label.rsplit("_", 1)[0]
        hidden.append({"placeholder": f"[{label}]", "value": vault.get(label), "kind": KIND_LABEL.get(kind, kind),
                       "where": _where(label, question=question_sent, system=system, inputs=inputs_text,
                                       data=data_text, history=history_sent, answer=reply)})
    by_kind: Dict[str, int] = {}
    for h in hidden:
        by_kind[h["kind"]] = by_kind.get(h["kind"], 0) + 1

    found = call.get("sentinel")
    return {
        "purpose": call.get("purpose"),
        "summary": {"sentinel_pass": (found == {}) if isinstance(found, dict) else None,
                    "sentinel_found": found if isinstance(found, dict) and found else None,
                    "hidden": len(hidden), "hidden_by_kind": by_kind, "lookups": lookups,
                    "figures_checked": checked, "figures_changed": changed},
        "hidden": hidden,
        "question": {"raw": question_raw, "sent": question_sent} if question_sent else None,
        "earlier_turns": earlier_turns,
        "steps": steps,
        "answer": {"model": _readable(reply),
                   "user": _readable(call.get("reply_user")) if call.get("raw") else None},
    }


def attach(snap: Dict[str, Any]) -> Dict[str, Any]:
    """The snapshot with a story on each call (story=...). Never raises: a call it cannot read gets none."""
    for call in snap.get("calls") or []:
        try:
            call["story"] = build(call, snap.get("vault") or {})
        except Exception:  # noqa: BLE001 - the full record still shows
            call["story"] = None
    return snap
