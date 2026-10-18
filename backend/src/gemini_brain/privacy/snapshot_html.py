"""
snapshot_html.py — One privacy snapshot as a self-contained HTML page (PII plan, phase 4).

The page an admin downloads from the privacy panel and shares, or prints to PDF. For each model call it shows
four panes side by side: what the request held (real values, highlighted red), what the model was sent
(placeholders, highlighted amber), what the model wrote back, and what the user saw. Then the sentinel's verdict
on what was sent. No scripts and no outside resources.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List

from gemini_brain.privacy.vault import TOKEN_RX

PURPOSE = {"agent": "Chat answer", "agent_law": "VAT law answer", "report_narration": "Report summary"}


def _text_of(messages: Any) -> str:
    """A conversation as readable text: each turn's role, its text, the tools called and what they returned."""
    out: List[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        out.append(f"── {str(m.get('role', '')).upper()} ──")
        for block in m.get("content") or []:
            if not isinstance(block, dict):
                continue
            if "text" in block:
                out.append(str(block["text"]))
            if "toolUse" in block:
                use = block["toolUse"] or {}
                out.append(f"→ tool {use.get('name')}: {json.dumps(use.get('input'), ensure_ascii=False)}")
            if "toolResult" in block:
                for part in (block["toolResult"] or {}).get("content") or []:
                    if isinstance(part, dict) and "json" in part:
                        out.append("← result: " + json.dumps(part["json"], ensure_ascii=False, indent=1))
                    elif isinstance(part, dict) and "text" in part:
                        out.append("← result: " + str(part["text"]))
            for kind in ("document", "image"):
                if kind in block:
                    out.append(f"[attached {kind}: sent as is, see uploads]")
    return "\n".join(out)


def _plain(text: str) -> str:
    """A model reply without markdown emphasis markers (**bold**, __bold__), which read as noise in a pane."""
    return re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", text or "")


def _when(ts: Any) -> str:
    """The snapshot's time in the reporting time zone (REPORT_TIMEZONE), labelled with it."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        from gemini_brain.config.settings import settings
        zone = settings.report_timezone or "UTC"
        when = datetime.datetime.fromisoformat(str(ts)).astimezone(ZoneInfo(zone))
        return f"{when:%d %b %Y %H:%M} ({zone})"
    except Exception:  # noqa: BLE001
        return str(ts)


def _mark_tokens(text: str) -> str:
    return TOKEN_RX.sub(lambda m: f'<mark class="tok">{m.group(0)}</mark>', html.escape(text))


def _mark_values(text: str, values: List[str]) -> str:
    escaped = html.escape(text)
    found = sorted({html.escape(v) for v in values if v and len(v) >= 3}, key=len, reverse=True)
    if not found:
        return escaped
    rx = re.compile("|".join(re.escape(v) for v in found), re.IGNORECASE)
    return rx.sub(lambda m: f'<mark class="raw">{m.group(0)}</mark>', escaped)


def render(snap: Dict[str, Any]) -> str:
    values = list((snap.get("vault") or {}).values())
    stats = snap.get("stats") or {}
    hidden = stats.get("hidden") or {}
    calls_html = []
    for i, call in enumerate(snap.get("calls") or [], 1):
        found = call.get("sentinel")
        verdict = ("<span class='pill ok'>Sentinel: nothing personal sent</span>" if found == {} else
                   f"<span class='pill bad'>Sentinel found: {html.escape(json.dumps(found))}</span>" if found else
                   "<span class='pill'>Sentinel: not run</span>")
        raw = call.get("raw")
        raw_text = _text_of((raw or {}).get("messages")) if raw else None
        sent = call.get("sent") or {}
        sent_text = _text_of(sent.get("messages"))
        panes = [
            ("1 · What the request held",
             "Real values, never sent. Kept encrypted, admins only. The model's own turns keep its placeholders.",
             _mark_values(raw_text, values) if raw_text is not None else
             "<em>Not kept: no encryption key, or older than the retention period.</em>"),
            ("2 · Sent to the model" if snap.get("mode") == "on" else "2 · Would be sent (shadow mode)",
             "Exactly what the model received.", _mark_tokens(sent_text)),
            ("3 · Model's reply", "As the model wrote it.", _mark_tokens(_plain(str(call.get("reply_model") or "")))),
            ("4 · What the user saw", "Placeholders swapped back on our server.",
             _mark_values(_plain(str(call.get("reply_user") or "")), values) if raw is not None else
             "<em>Not kept (raw side).</em>"),
        ]
        instructions = (f"<details><summary>AI instructions (system prompt) as sent</summary>"
                        f"<pre>{html.escape(sent.get('system', ''))}</pre></details>") if sent.get("system") else ""
        calls_html.append(
            f"<section><h2>Call {i}: {html.escape(PURPOSE.get(call.get('purpose'), str(call.get('purpose'))))}</h2>"
            f"<p>{verdict}</p><div class='panes'>"
            + "".join(f"<div class='pane'><h3>{h}</h3><p class='note'>{n}</p><pre>{body}</pre></div>"
                      for h, n, body in panes)
            + "</div>" + instructions + "</section>")
    summary = ", ".join(f"{v} {k.lower().replace('_', ' ')}" for k, v in sorted(hidden.items())) or "none"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Privacy snapshot {html.escape(str(snap.get('id')))}</title>
<style>
body{{font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#18201f;background:#fff;margin:24px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 6px}} h3{{font-size:12px;letter-spacing:.06em;text-transform:uppercase;margin:0}}
.meta{{color:#5b6866;margin:0 0 6px}} .note{{color:#5b6866;font-size:12px;margin:2px 0 8px}}
.panes{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}
.pane{{border:1px solid #d9dfdd;border-radius:8px;padding:10px;min-width:0}}
details{{margin-top:8px;font-size:12px;color:#5b6866}} summary{{cursor:pointer}}
pre{{white-space:pre-wrap;word-break:break-word;font:11.5px/1.5 ui-monospace,Consolas,monospace;background:#f6f7f6;padding:8px;border-radius:6px;max-height:640px;overflow:auto;margin:0}}
mark.tok{{background:#fdf0d2;color:#7a4d00}} mark.raw{{background:#fbe7e4;color:#b3261e}}
.pill{{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;background:#eef0ef}}
.ok{{background:#e2f3e7;color:#1f7a3d}} .bad{{background:#fbe7e4;color:#b3261e}}
@media print{{pre{{max-height:none}} .panes{{grid-template-columns:1fr 1fr}}}}
</style></head><body>
<h1>Privacy snapshot</h1>
<p class="meta">Request {html.escape(str(snap.get('id')))} · {html.escape(_when(snap.get('ts')))} · {html.escape(str(snap.get('user_label') or f"user {snap.get('user_id')}"))}
 · {html.escape(', '.join(snap.get('organization_names') or [str(o) for o in snap.get('organization_ids') or []]))} · PII_MODE {html.escape(str(snap.get('mode')))}</p>
<p class="meta">Values hidden: {html.escape(summary)}. Placeholders left unresolved: {stats.get('unresolved', 0)}.
 Amounts, VAT, dates and document numbers stay visible by design.</p>
<p class="meta"><mark class="raw">red</mark> real value · <mark class="tok">amber</mark> placeholder</p>
{''.join(calls_html)}
</body></html>"""
