"""Document upload evaluation (docs/DOCUMENT_UPLOAD_PLAN.md, phase 5).

    python scripts/eval/upload_eval.py build --out DIR [--real DIR] [--books books.json]
    python scripts/eval/upload_eval.py read  --dir DIR [--only id,id]          # the reader, Bedrock only
    python scripts/eval/upload_eval.py agent --dir DIR [--org 24] [--no-books] # questions to the agent

`read` uploads every file as the app does (checks, store, reader on Bedrock) and compares the record with the truth
of the case (scripts/eval/upload_cases.py): fields, code checks, statement lines, text. `agent` puts each case's
questions to the agent with the file attached and checks the answer by rule; the cases built from the books need Cube
(run it on the VM; `--no-books` runs the rest anywhere). Answers still need a hand grade: the report lists them.
Results go to DIR/results_<section>.json and DIR/report_<section>.md. Calls Bedrock: it costs money (about $1 to $2).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(Path(__file__).resolve().parent)]

BILL_FIELDS = ("vendor", "trn", "number", "date", "currency", "subtotal", "tax", "total", "items")
#: The six fields a user relies on; the exit check counts files that get all of them right.
KEY_FIELDS = ("vendor", "trn", "number", "date", "tax", "total")
DEFAULT_ORG_NAME = "Northwind Trading LLC"


# ── scoring (pure: tested in tests/unit/test_upload_eval.py) ────────────────────

def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _numbers_ok(got: Any, want: Any) -> bool:
    wants = want if isinstance(want, list) else [want]
    return isinstance(got, (int, float)) and any(abs(float(got) - float(w)) < 0.01 for w in wants)


def score_bill(truth: Dict[str, Any], extract: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    """Per field: True, False, or None when the truth does not say. `extract` is DocumentExtract.to_dict()."""
    out: Dict[str, Optional[bool]] = {f: None for f in BILL_FIELDS}
    if "vendor" in truth:
        name = str(extract.get("vendor_name") or "").lower()
        out["vendor"] = any(w.lower() in name for w in truth["vendor"])
    if "trn" in truth:
        got = _norm(extract.get("vendor_tax_id"))
        out["trn"] = (not got) if truth["trn"] is None else got == _norm(truth["trn"])
    if "number" in truth:
        out["number"] = _norm(extract.get("document_number")) == _norm(truth["number"])
    if "date" in truth:
        out["date"] = extract.get("date") == truth["date"]
    if "currency" in truth:
        out["currency"] = str(extract.get("currency") or "").upper() in [c.upper() for c in truth["currency"]]
    for field, key in (("subtotal", "subtotal"), ("tax", "tax_total"), ("total", "grand_total")):
        if field in truth:
            out[field] = _numbers_ok(extract.get(key), truth[field])
    if "items" in truth:
        got = sorted(round(float(i["amount"]), 2) for i in extract.get("items") or [] if isinstance(i.get("amount"), (int, float)))
        out["items"] = got == sorted(round(float(a), 2) for a in truth["items"])
    return out


def score_checks(expected: Dict[str, str], checks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    got = {c["name"]: c["status"] for c in checks or []}
    return {name: {"expected": status, "got": got.get(name), "ok": got.get(name) == status} for name, status in expected.items()}


def score_statement(truth: Dict[str, Any], extract: Dict[str, Any]) -> Dict[str, Any]:
    lines = extract.get("statement_lines") or []
    refs = {_norm(ln.get("reference")) for ln in lines}
    want = [_norm(r) for r in truth["references"]]
    return {
        "lines": {"got": len(lines), "want": truth["lines"], "ok": len(lines) == truth["lines"]},
        "references": {"found": sum(1 for r in want if r in refs), "want": len(want), "ok": all(r in refs for r in want)},
        "opening": {"got": extract.get("opening_balance"), "want": truth["opening"], "ok": _numbers_ok(extract.get("opening_balance"), truth["opening"])},
        "closing": {"got": extract.get("closing_balance"), "want": truth["closing"], "ok": _numbers_ok(extract.get("closing_balance"), truth["closing"])},
        "debits": {"got": round(sum(ln.get("debit") or 0 for ln in lines), 2), "want": truth["debits"],
                   "ok": abs(sum(ln.get("debit") or 0 for ln in lines) - truth["debits"]) < 0.05},
        "credits": {"got": round(sum(ln.get("credit") or 0 for ln in lines), 2), "want": truth["credits"],
                    "ok": abs(sum(ln.get("credit") or 0 for ln in lines) - truth["credits"]) < 0.05},
    }


def score_text(truth: Dict[str, Any], extract: Dict[str, Any]) -> Dict[str, bool]:
    text = re.sub(r"[\s|]+", " ", str(extract.get("text") or "")).lower().replace(",", "")
    return {p: re.sub(r"[\s|,]+", " ", p).lower() in text for p in truth.get("phrases", [])}


def judge_read(case: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    """The verdict on one read: {'ok': bool, 'detail': ...}."""
    section, truth, expect = case["section"], case.get("truth") or {}, case.get("expect") or {}
    if section == "refuse":
        return {"ok": run.get("refused") == expect.get("code"), "detail": {"got": run.get("refused"), "want": expect.get("code")}}
    if run.get("refused"):
        return {"ok": False, "detail": {"refused": run["refused"]}}
    if run.get("status") != "ready":
        return {"ok": False, "detail": {"status": run.get("status"), "reason": run.get("reason")}}
    payload = run["payload"]
    extract = payload["extract"]
    kind = truth.get("kind")
    if kind == "bill":
        fields = score_bill(truth, extract)
        checks = score_checks((expect.get("checks") or {}), payload.get("checks") or [])
        ok = all(v is not False for v in fields.values()) and all(c["ok"] for c in checks.values())
        key6 = all(fields[f] is not False for f in KEY_FIELDS) and any(fields[f] for f in KEY_FIELDS)
        return {"ok": ok, "key6": key6, "detail": {"fields": fields, "checks": checks, "note": run.get("reason", "")}}
    if kind == "statement":
        parts = score_statement(truth, extract)
        return {"ok": all(p["ok"] for p in parts.values()), "detail": {**parts, "note": run.get("reason", "")}}
    if kind == "text":
        phrases = score_text(truth, extract)
        source_ok = expect.get("source") in (None, payload.get("source"))
        return {"ok": all(phrases.values()) and source_ok, "detail": {"phrases": phrases, "source": payload.get("source")}}
    return {"ok": False, "detail": {"unknown_kind": kind}}


def judge_answer(question: Dict[str, Any], case: Dict[str, Any], answer: str, tool_names: List[str],
                 grounded: Optional[bool]) -> Dict[str, Any]:
    """Rule checks on one answer. A hand grade is still needed; these catch the plain failures."""
    low = (answer or "").lower()
    verdicts: Dict[str, bool] = {}
    for needle in question.get("must", []):
        verdicts[f"says {needle}"] = str(needle).lower() in low
    if question.get("must_any"):
        verdicts["says one of: " + " / ".join(question["must_any"][:3])] = any(n.lower() in low for n in question["must_any"])
    for needle in question.get("must_not", []):
        verdicts[f"does not say {needle}"] = str(needle).lower() not in low
    if question.get("needs_call"):
        verdicts[f"called {question['needs_call']}"] = question["needs_call"] in tool_names
    if question.get("no_tools"):
        verdicts["called no tool"] = not [t for t in tool_names if t != "read_document"]
    if question.get("must_diff"):
        diff = (case.get("truth") or {}).get("diff") or {}
        verdicts[f"names {diff.get('missing_from_statement')}"] = str(diff.get("missing_from_statement", "")).lower() in low
        verdicts[f"names {diff.get('extra_on_statement')}"] = str(diff.get("extra_on_statement", "")).lower() in low
    if grounded is not None:
        verdicts["figures grounded"] = bool(grounded)
    return {"ok": all(verdicts.values()), "verdicts": verdicts}


def summarize_read(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    reads = [r for r in results if r["section"] in ("read", "refuse")]
    bills = [r for r in reads if (r.get("verdict") or {}).get("key6") is not None]
    times = sorted(r["seconds"] for r in reads if r.get("seconds") and r.get("model_read"))
    return {
        "cases": len(reads), "passed": sum(1 for r in reads if r["verdict"]["ok"]),
        "bills": len(bills), "bills_key6": sum(1 for r in bills if r["verdict"]["key6"]),
        "cost_usd": round(sum(r.get("cost") or 0 for r in reads), 4),
        "p95_seconds_model_read": round(times[min(len(times) - 1, int(len(times) * 0.95))], 1) if times else None,
        "median_seconds_model_read": round(statistics.median(times), 1) if times else None,
    }


# ── running ─────────────────────────────────────────────────────────────────────

def _setup() -> Any:
    from gemini_brain.config.settings import settings

    settings.upload_dir = tempfile.mkdtemp(prefix="upload-eval-")
    settings.upload_daily_pages = 100_000   # the cap is for users, not for the evaluation
    settings.uploads_enabled = True
    return settings


def upload_and_read(path: Path, user_id: int = 0, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Upload a file as the app does and read it. Returns the run: status, payload, seconds, cost, or `refused`."""
    from gemini_brain.uploads import reader, store
    from gemini_brain.uploads.validate import UploadRejected, inspect

    content = path.read_bytes()
    try:
        inspected = inspect(content, path.name)
    except UploadRejected as e:
        return {"refused": e.code, "message": e.message}
    rec = store.put(content, filename=path.name, inspected=inspected, user_id=user_id, session_id=session_id or str(uuid.uuid4()))
    store.update(rec.id, status="reading", read_started_at=time.time())
    t0 = time.perf_counter()
    reader.read_now(rec.id)
    seconds = time.perf_counter() - t0
    rec = store.get(rec.id)
    payload = store.load_extract(rec.id)
    return {"id": rec.id, "status": rec.status, "reason": rec.failure_reason, "payload": payload, "seconds": round(seconds, 1),
            "cost": (payload or {}).get("usage", {}).get("cost_usd", 0), "model_read": bool(payload and payload.get("source") == "claude"),
            "pages": rec.pages}


def run_read(cases: List[Dict[str, Any]], directory: Path, workers: int = 4) -> List[Dict[str, Any]]:
    def one(case: Dict[str, Any]) -> Dict[str, Any]:
        try:
            run = upload_and_read(directory / case["file"])
        except Exception as e:  # one broken case must not lose the others' results
            run = {"status": "failed", "reason": f"harness error: {type(e).__name__}: {e}"}
        verdict = judge_read(case, run)
        print(f"  {'PASS' if verdict['ok'] else 'FAIL'}  {case['id']:<32} {run.get('seconds', '-')}s  ${run.get('cost') or 0:.4f}", flush=True)
        return {"id": case["id"], "section": case["section"], "file": case["file"], "verdict": verdict, **{
            k: run.get(k) for k in ("status", "reason", "refused", "seconds", "cost", "model_read", "pages")}, "extract": (run.get("payload") or {}).get("extract")}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, [c for c in cases if c["section"] in ("read", "refuse") and not c.get("needs_books")]))


def run_agent_cases(cases: List[Dict[str, Any]], directory: Path, org_id: int, include_books: bool) -> List[Dict[str, Any]]:
    from gemini_brain.agent import loop
    from gemini_brain.config.settings import settings
    from gemini_brain.uploads import context

    out = []
    for case in cases:
        if not case.get("questions") or (case.get("needs_books") and not include_books):
            continue
        session = str(uuid.uuid4())
        run = upload_and_read(directory / case["file"], user_id=0, session_id=session)
        if run.get("refused") or run.get("status") != "ready":
            out.append({"id": case["id"], "error": f"file not read: {run.get('refused') or run.get('reason')}"})
            continue
        from gemini_brain.uploads import store

        doc = context.AttachedDocument(store.get(run["id"]), run["payload"])
        meta = {org_id: {"name": case.get("org_name") or DEFAULT_ORG_NAME, "currency": "AED"}}
        block = context.prompt_block([doc], context.own_names(meta, [org_id]))
        for question in case["questions"]:
            t0 = time.perf_counter()
            result = loop.run_agent(question["q"], [org_id], meta, documents=block, document_ids=(doc.rec.id,),
                                    document_evidence=context.evidence([doc], {}), user_id=0, session_id=session,
                                    subject="upload-eval")
            names = [c["name"] for c in result.tool_calls]
            verdict = judge_answer(question, case, result.answer, names, (result.verification or {}).get("grounded"))
            print(f"  {'PASS' if verdict['ok'] else 'FAIL'}  {case['id']:<32} {question['q'][:48]!r}", flush=True)
            out.append({"id": case["id"], "question": question["q"], "answer": result.answer, "tools": names, "verdict": verdict,
                        "status": result.status, "seconds": round(time.perf_counter() - t0, 1),
                        "cost": (result.usage or {}).get("cost_usd", 0) + (run.get("cost") or 0),
                        "grounded": (result.verification or {}).get("grounded")})
    _ = settings
    return out


# ── reports ─────────────────────────────────────────────────────────────────────

def read_report(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    lines = ["# Upload evaluation: reading", "",
             f"Cases {summary['passed']}/{summary['cases']} pass. Bills with the six key fields right: "
             f"{summary['bills_key6']}/{summary['bills']}. Cost ${summary['cost_usd']}. "
             f"Model reads: median {summary['median_seconds_model_read']} s, p95 {summary['p95_seconds_model_read']} s.", "",
             "| Case | Result | Seconds | Cost | What failed |", "|---|---|---|---|---|"]
    for r in results:
        d = r["verdict"]["detail"]
        failed = [k for k, v in (d.get("fields") or {}).items() if v is False] + \
                 [f"check {k}" for k, v in (d.get("checks") or {}).items() if not v["ok"]] + \
                 [k for k, v in d.items() if isinstance(v, dict) and v.get("ok") is False and k not in ("fields", "checks")] + \
                 [f"phrase {k!r}" for k, v in (d.get("phrases") or {}).items() if not v]
        if r.get("status") == "failed":
            failed.append(f"not read: {r.get('reason')}")
        lines.append(f"| {r['id']} | {'pass' if r['verdict']['ok'] else 'FAIL'} | {r.get('seconds') or ''} | "
                     f"{('$%.4f' % r['cost']) if r.get('cost') else ''} | {', '.join(failed)} |")
    return "\n".join(lines) + "\n"


def agent_report(results: List[Dict[str, Any]]) -> str:
    lines = ["# Upload evaluation: answers", "", f"{sum(1 for r in results if r.get('verdict', {}).get('ok'))}/{len(results)} pass by rule. Grade each answer by hand too.", ""]
    for r in results:
        if r.get("error"):
            lines += [f"## {r['id']}: ERROR", r["error"], ""]
            continue
        v = r["verdict"]
        lines += [f"## {r['id']}: {'pass' if v['ok'] else 'FAIL'}", f"**Q:** {r['question']}  ",
                  f"Tools: {', '.join(r['tools']) or 'none'} · {r['seconds']} s · ${r['cost']:.4f}", "",
                  *[f"- {'ok' if ok else 'FAILED'}: {name}" for name, ok in v["verdicts"].items()], "", "> " + r["answer"].replace("\n", "\n> "), ""]
    return "\n".join(lines)


# ── command line ────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--real", type=Path, help="folder with the public receipt photos named in upload_cases.REAL_PHOTOS")
    b.add_argument("--books", type=Path, help="fetch_books_samples.py output, for the cases built from the books")
    for name in ("read", "agent"):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True, type=Path)
        p.add_argument("--only", help="comma-separated case ids")
    sub.choices["agent"].add_argument("--org", type=int, default=24)
    sub.choices["agent"].add_argument("--no-books", action="store_true", help="skip the cases that need Cube")
    args = ap.parse_args()

    import upload_cases as uc

    if args.cmd == "build":
        books = json.loads(args.books.read_text(encoding="utf-8")) if args.books else None
        cases = uc.build_files(args.out, args.real, books)
        (args.out / "manifest.json").write_text(json.dumps([c.to_dict() for c in cases], indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"built {len(cases)} cases in {args.out}")
        return

    manifest = json.loads((args.dir / "manifest.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        manifest = [c for c in manifest if c["id"] in wanted]
    _setup()
    if args.cmd == "read":
        results = run_read(manifest, args.dir)
        summary = summarize_read(results)
        (args.dir / "results_read.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (args.dir / "report_read.md").write_text(read_report(results, summary), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    else:
        results = run_agent_cases(manifest, args.dir, args.org, include_books=not args.no_books)
        (args.dir / "results_agent.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (args.dir / "report_agent.md").write_text(agent_report(results), encoding="utf-8")
        ok = sum(1 for r in results if r.get("verdict", {}).get("ok"))
        print(f"{ok}/{len(results)} answers pass by rule; cost ${sum(r.get('cost') or 0 for r in results):.3f}")


if __name__ == "__main__":
    main()
